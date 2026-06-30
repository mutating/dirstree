# Transactional crawling for `dirstree`

## Context

Сегодня `Crawler` обходит директорию как генератор, без гарантии консистентности. `freeze=True` (snapshot заранее) лишь смещает проблему: snapshot собирается не атомарно, и изменения после него неотличимы от отсутствия.

Цель — опциональный режим, в котором любое значимое изменение в наблюдаемой области во время обхода прерывает итерацию исключением. Реализация — через `watchdog` (фоновый поток событий ФС) + `cantok.ConditionToken`, который встраивается в существующую токен-цепочку. Поведение по умолчанию не меняется. Многопоточность изолирована: два параллельных обхода одной директории не должны мешать друг другу.

Принятые решения:
- События: **created + deleted + moved + modified**.
- Те же фильтры (`extensions`, `exclude`, `filter`), что у краулера, применяются к событиям.
- `watchdog` — **опциональная** зависимость через `pip install dirstree[transactional]`.
- Имя — `transaction`. API: `apply(transactional=True)` и `with crawler.transaction as scope:`.

**Внешние зависимости и сущности, упоминаемые в плане:**
- Все токены/исключения cancellation (`DefaultToken`, `ConditionToken`, `SimpleToken`, `CancellationError`) — из существующей зависимости [`cantok`](https://pypi.org/project/cantok/).
- `pathspec.PathSpec` (для `_compile_excludes`) — из существующей `pathspec`.
- `describe_data_object` (для `__repr__`) — из существующей `printo`.
- `PossibleCallMatcher` (для валидации callable в `apply`) — из существующей `sigmatch`.
- `match` (для `pytest.raises(..., match=match('exact text'))`) — из существующей dev-зависимости `full_match`.
- `LockTraceWrapper` — из НОВОЙ dev-зависимости `locklib` (см. ниже в разделе «Изменяемые файлы»).
- `suby.run` — из НОВОЙ dev-зависимости `suby` ([github.com/mutating/suby](https://github.com/mutating/suby)), используется в shutdown-тестах для запуска изолированного интерпретатора с timeout-контролем. API: `result = run(cmd, timeout=5, catch_exceptions=True)` → `SubprocessResult` с `.stdout`, `.returncode`, `.killed_by_token`, `.id`.
- **`watchdog.observers.Observer`** — главный объект библиотеки `watchdog`. Использует под капотом фоновый `threading.Thread` для прослушивания нативного механизма ФС-нотификаций (inotify на Linux, FSEvents на macOS, ReadDirectoryChangesW на Windows). API: `observer.schedule(handler, path, recursive=True)` для подписки, `observer.start()` для активации фонового потока, `observer.stop() + observer.join(timeout)` для остановки. В нашей реализации Observer создаётся в body `_observer` cached_property на первый доступ из `TC._start()` под `_lifecycle_lock`. Ему отдаётся наш `FilteredEventHandler`, и через `schedule()` подписываемся на все `source.paths`. Handler в фоновом потоке ловит события, прогоняет через те же фильтры, что у источника, и при срабатывании выставляет `threading.Event`, который читает `ConditionToken` в основном потоке итерации.

**Язык кода**: **все строки внутри исходного кода** (error messages, docstrings, комментарии) — **на английском**. Русские сообщения в плане ниже — только для удобства обсуждения; в реальный код пойдут английские варианты. Соответственно тесты ассертят на английские строки через `match=match(...)`.

## Подход (high-level)

Один класс — `TransactionalCrawler(AbstractCrawler)`. Он же сам себе context manager.

`AbstractCrawler.transaction` — `@property` (не `cached_property`). Для **всех источников кроме `TransactionalCrawler`** (Crawler, PythonCrawler, CrawlersGroup) на каждый доступ возвращает **новый** `TransactionalCrawler(self)` — это обеспечивает thread-safety (`c.transaction is c.transaction → False`). **TransactionalCrawler переопределяет**: на **активном** TC возвращает self для идемпотентности композиции (`scope.transaction.transaction.transaction is scope` пока scope активен — внутри `with`-блока); на **неактивном** TC (fresh или stopped) кидает `TransactionInactiveError`. Это намеренное исключение для удобства, не нарушение контракта — пользователь получает «активную транзакцию» в обоих случаях (новый TC у обычных источников, тот же self у активного TC), но identity отличается. **CrawlersGroup не переопределяет** — наследует базовое поведение, и `group.transaction` возвращает `TransactionalCrawler(group)` без рекурсивного оборачивания children.

**Про `ConditionToken` из cantok**: это poll-based примитив. Лямбда, переданная в `ConditionToken(predicate)`, вызывается **каждый раз**, когда внешний код выполняет `token.check()` / `bool(token)` / `_check_token`. Если predicate вернул False — токен считается cancelled. У нас источник (`Crawler.go`) делает `_check_token(merged)` между каждым `yield` — то есть наши condition-lambdas пробегаются между каждым отданным path'ом.

**Централизованная схема обработки событий**:
- TC всегда имеет **один Observer** и **один общий `ChangeAlarm`** (`threading.Event` + `Lock` + данные первого события).
- В cached_property `_observer` (вызывается из `_start()`) обходим source через внутренний хелпер `unwrap_to_crawlers(source)`:
  - Рекурсивно разворачивает `CrawlersGroup` до листовых `Crawler`-ов.
  - Транзитивно разворачивает вложенные `TransactionalCrawler` до их `.source` (corner-case: пользователь вручную вложил TC в group, см. тест `test_active_tc_inside_group_is_unwrapped_to_its_source`).
- Для каждого листа создаётся **отдельный `FilteredEventHandler`** с собственными `extensions`/`exclude`/`filter`, но **shared** `ChangeAlarm`.
- Все handlers привязываются к одному Observer через `observer.schedule(handler, leaf.paths, recursive=True)`.
- Любое событие в любом листе → handler пишет в общий `ChangeAlarm` (first-wins) → ConditionToken в `go()` видит → обход прерывается.
- Никаких агрегаций, никаких branches по типу source.

`TransactionalCrawler`:
- Хранит ссылку на источник, `_active`, `_observer`, `_change_alarm`.
- `__enter__` → `self._start()`, `__exit__` → `self._stop()`; документированный путь — `with crawler.transaction as scope:`. Полный контракт lifecycle'а (call sites, escape-hatch, asymmetry, state-machine guards) — в секции «API» ниже.
- `go()` — единый код-путь для одиночного Crawler и для CrawlersGroup. Логика разворачивания листьев — внутри cached_property `_observer`.
- Все публичные методы итерации (`go`, `__iter__`, `apply`) при `_active=False` кидают `TransactionInactiveError`. **Исключение для `TC.apply`**: если в вызов передан `transactional=False` (kwarg), поднимается `InvalidTransactionalArgumentError` **раньше** проверки `_active` (mis-use detection приоритетнее lifecycle check). То есть `inactive_tc.apply(fn, transactional=False)` → `InvalidTransactionalArgumentError`, не `TransactionInactiveError`. Это intentional: validation-ошибка важнее state-ошибки.

Thread-safety: fresh-TC-per-access (см. выше) делает параллельные `with c.transaction as scope:` независимыми; вложенные `with` тоже. Observer живёт **всю транзакцию**, не per-`go()` — изменение между двумя последовательными `for path in scope:` циклами в одном `with` будет зафиксировано.

## Структура файлов

**Новые:**
- `dirstree/crawlers/transactional/__init__.py` — пусто. Никаких re-exports: `TransactionalCrawler` — внутренний класс (появляется только как промежуточная сущность через `crawler.transaction`), не входит в публичный API.
- `dirstree/crawlers/transactional/alarm.py` — класс `ChangeAlarm` (thread-safe holder первой причины: `threading.Event` + `Lock` + данные сообщения). Без watchdog-зависимостей. Изолированный модуль, чтобы `ChangeAlarm` можно было тестировать unit'ами без транзакционного контекста.
- `dirstree/crawlers/transactional/crawler.py` — класс `TransactionalCrawler`. Импортирует `ChangeAlarm` локально (`from dirstree.crawlers.transactional.alarm import ChangeAlarm`). Содержит `@staticmethod @functools.lru_cache(maxsize=None) _load_watchdog()` — **единственную точку `import watchdog` во всём пакете**. Внутри `_load_watchdog` после успешного импорта определяется (через class factory pattern) и возвращается `FilteredEventHandler(FileSystemEventHandler)` — это **единственный** класс, содержащий watchdog-импорт; никакого отдельного `handler.py` файла. `lru_cache` обеспечивает, что класс handler'а создаётся ровно один раз за жизнь процесса.
- `issue_self.md` (корень проекта) — содержимое описано в отдельной секции «issue_self.md» ниже.
- `tests/units/__init__.py`, `tests/units/crawlers/__init__.py`, `tests/units/crawlers/transactional/__init__.py` — пустые `__init__.py` для зеркальной структуры под `tests/units/` (см. правило в CLAUDE.md ниже).
- `tests/units/crawlers/transactional/test_crawler.py` — основной сьют для transactional. Stress-тесты живут в этом же файле под маркером `@pytest.mark.stress` (не в отдельном файле).
- `tests/python/__init__.py`, `tests/python/interpreter/__init__.py`, `tests/python/dependencies/__init__.py` — двухуровневая структура для тестов **инвариантов**, на которые опирается реализация:
  - `tests/python/interpreter/test_atomicity.py` — инварианты CPython (атомарность/visibility plain-attribute writes).
  - `tests/python/interpreter/test_atexit.py` — инварианты `atexit` (срабатывает на `sys.exit`, не срабатывает на `os._exit`, разрешает unregister-from-handler).
  - `tests/python/dependencies/test_cantok.py` — инварианты внешней зависимости `cantok` (composition: identity sub-token'а в `exc.token`; `raise X from Y` для custom `raise_on_cancel`).
  - `tests/python/dependencies/test_watchdog.py` — инварианты `watchdog`: по одному тесту на каждый важный аспект API, на который мы опираемся.

Все три файла подробно расписаны в секции «Python invariant tests» ниже.

**Перемещение существующих тестов:**
- `tests/test_crawler.py` → `tests/units/crawlers/test_crawler.py`
- `tests/test_python_crawler.py` → `tests/units/crawlers/test_python_crawler.py`
- `tests/test_errors.py` → `tests/units/test_errors.py`
- `tests/conftest.py` — оставить в `tests/` (доступен из всех subdirs) или скопировать в `tests/units/conftest.py` — выбрать по pytest-конвенциям.
- `tests/test_files/` (фикстурные директории) — оставить как есть; пути в `conftest.py` поправить если нужно.

**Изменяемые:**
- `dirstree/errors.py` — шесть новых классов, все минимальные (без custom `__init__`, по стилю существующего `IncompatibleCrawlerOptionsError`): `TransactionalChangeError(DirstreeError)`, `TransactionInactiveError(DirstreeError)` (объединяет «never started» и «already stopped» — оба случая означают «TC не работает; recovery — новый TC через `original_crawler.transaction`»; различаются только текстом сообщения для диагностики), `TransactionAlreadyActiveError(DirstreeError)`, `TransactionStoppedDuringIterationError(DirstreeError)`, `InvalidTransactionalArgumentError(DirstreeError)`, `WatchdogNotInstalledError(DirstreeError, ImportError)`. Вся диагностика — в тексте сообщения.
- `dirstree/crawlers/abstract.py` — `transaction` через обычный `@property` (каждый доступ возвращает свежий `TransactionalCrawler`); расширить `apply` параметром `transactional: bool = False`. Лениво импортирует `TransactionalCrawler` внутри property (`from dirstree.crawlers.transactional.crawler import TransactionalCrawler`), чтобы избежать circular import.
- `dirstree/crawlers/crawler.py` — вынести `pathspec.PathSpec.from_lines('gitwildmatch', self.exclude)` в helper на классе `Crawler`:
  ```python
  def _compile_excludes(self) -> pathspec.PathSpec:
      return pathspec.PathSpec.from_lines('gitwildmatch', self.exclude)
  ```
  Использование: `spec.match_file(str(path)) -> bool` (True если path matches любой из exclude-pattern'ов). Текущий код в `_traverse` (`crawler.py:157`) делает компиляцию inline — переносим в helper, в обоих местах (`_traverse` и `FilteredEventHandler`) вызываем единый метод. Это гарантирует, что фильтр в watcher'е поведенчески идентичен фильтру в обходе. Конкретное изменение в `_traverse`:
  ```python
  # crawler.py:157, было:
  excludes_spec = pathspec.PathSpec.from_lines('gitwildmatch', self.exclude)
  # станет:
  excludes_spec = self._compile_excludes()
  ```
- `dirstree/crawlers/group.py` — **без изменений**: `transaction` наследуется из `AbstractCrawler` и сразу даёт нужное поведение (`TransactionalCrawler(group)`). `apply(fn, transactional=True)` тоже наследуется напрямую — внутри `AbstractCrawler.apply` сделано `with self.transaction as scope: for path in scope.go(token): function(path)`, что для группы работает идентично — оборачивает группу в outer TC, делегирует. Тест `test_crawlers_group_apply_transactional_true_works_end_to_end` (см. ниже) явно это покрывает.
- `dirstree/__init__.py` — публичный re-export **пяти** исключений: `TransactionalChangeError`, `TransactionInactiveError`, `TransactionAlreadyActiveError`, `TransactionStoppedDuringIterationError` (user-facing: упоминается в README как сигнал для worker-потоков), `WatchdogNotInstalledError`. Шестое — `InvalidTransactionalArgumentError` — **внутренний** mis-use detector (поднимается только при некорректной передаче `transactional=False` в `TC.apply()`, что является ошибкой кода, а не runtime-сценарием для пользователя); доступно через прямой импорт из `dirstree.errors`. Все шесть — наследники `DirstreeError`, так что `except DirstreeError` ловит всё. `TransactionalCrawler` НЕ экспортируется (внутренний класс).
- `pyproject.toml` — `[project.optional-dependencies] transactional = ['watchdog==6.0.0']` (пин конкретной мажорной версии — мы опираемся на специфическое API и поведение watchdog, инварианты которого зафиксированы тестами в `tests/python/dependencies/test_watchdog.py`; апгрейд версии требует пересмотра этих инвариантов и их тестов). Зарегистрировать маркер `stress` в `[tool.pytest.ini_options] markers`, добавить `addopts = ["-m", "not stress"]` (массив toml — устойчивее к кавычкам, чем строка) чтобы основной прогон не подбирал stress-тесты.
- `requirements_dev.txt` — добавить **`locklib`** (для тестов корректности захвата `Lock`) и **`suby`** (упрощённый wrapper над subprocess, используется в shutdown-тестах). `locklib` — собственный пакет пользователя ([github.com/mutating/locklib](https://github.com/mutating/locklib)), предоставляет `LockTraceWrapper(lock)` — обёртка вокруг lock-объекта, добавляющая методы `notify(event_name: str)` и `was_event_locked(event_name: str, raise_exception: bool=True) -> bool`. `suby` — собственный пакет пользователя ([github.com/mutating/suby](https://github.com/mutating/suby)), даёт API `from suby import run` → `SubprocessResult(stdout, returncode, ...)` с встроенным `timeout=`. Используются **только** в тестах; в production-код не входят. **`watchdog` НЕ добавляется** в `requirements_dev.txt` — CI устанавливает сам пакет (`pip install .[transactional]`), и `watchdog==6.0.0` из `pyproject.toml`'s extras подтягивается транзитивно.
- `README.md` — расширить существующий раздел «Transactionality». Описать: `with crawler.transaction as scope:` и `apply(transactional=True)`, extras (`pip install dirstree[transactional]`), `TransactionalChangeError`. Явно отметить: (a) best-effort гарантия (платформенные latency-окна), (b) ресурсная стоимость (фоновый Observer-поток + ОС-подписка на каждую транзакцию), (c) multi-threading: безопасна одновременная итерация из нескольких потоков, но главный поток обязан дождаться workers до выхода из `with` (иначе workers получат `TransactionStoppedDuringIterationError`), (d) composition с user-token: отмена user-токеном → user-исключение, наш ConditionToken → всегда `TransactionalChangeError` (даже при custom `raise_on_cancel`), (e) **shutdown-поведение**: при graceful-выходе (KeyboardInterrupt / sys.exit / unhandled exception) `__exit__` вызывается через стек unwind, observer корректно останавливается; при abrupt-выходе (os._exit / SIGKILL / segfault) ОС реклaймит дескрипторы FS-нотификаций. Если по какой-то причине код обошёл `__exit__` (например, `tc.__enter__()` без последующего `__exit__()`, `os._exit()` из тела `with`, SIGKILL/segfault, и т.п.), есть **две слойные защиты**: (1) **graceful path** — `atexit`-хук вызывает `_stop()` при обычном завершении интерпретатора (`sys.exit`, normal return из main) для чистого закрытия watchdog-handle'ов; (2) **hard fallback** — `observer.daemon = True` не позволяет watchdog-потоку блокировать выход даже если atexit пропущен (`os._exit`, SIGKILL). atexit делает чистый shutdown, daemon защищает от зависания. Никаких ручных cleanup'ов пользователю не нужно.
- `CLAUDE.md` — добавить в раздел «Test conventions» **два** новых правила:
  1. «Unit-тесты лежат в `tests/units/` и **зеркалят** структуру `dirstree/`: для исходника `dirstree/X/Y.py` тест — `tests/units/X/test_Y.py`. Conftest и test-specific хелперы допустимы».
  2. «Тесты, проверяющие **инварианты**, на которые опирается реализация (свойства самого Python либо внешних зависимостей), лежат в `tests/python/` с двумя поддиректориями:
     - `tests/python/interpreter/` — инварианты CPython (атомарность операций, memory model и т.п.).
     - `tests/python/dependencies/` — инварианты конкретных внешних зависимостей (cantok, watchdog и т.п.).
     Структура внутри не зеркалит исходники: каждый файл соответствует **проверяемому свойству**, не модулю. Примеры: `tests/python/interpreter/test_atomicity.py`, `tests/python/dependencies/test_cantok.py`. В docstring'ах таких тестов обязательно: (а) на какую часть нашей реализации опирается инвариант, (б) что именно сломается у нас, если инвариант перестанет выполняться в будущей версии».

## API

```python
from dirstree import Crawler, TransactionalChangeError

crawler = Crawler('src/', extensions=['.py'], exclude=['build/**'])

# 1. with-форма
with crawler.transaction as scope:
    for path in scope:
        process(path)

# 2. apply-форма
crawler.apply(process, transactional=True)
```

Гарантии (контракт **«best-effort на стороне ОС, детерминирован на стороне нашей логики»**):
- **OS-side**: «best-effort» — доставка ФС-событий идёт через ОС-механизм с latency-окном до десятков мс на macOS FSEvents; единичное событие, произошедшее между мутацией и доставкой watchdog'у, может не успеть детектироваться до выхода из `with`. Это inherent OS limitation, см. секцию «Известные ограничения».
- **Library-side**: детерминировано — как только событие доставлено в наш handler, ConditionToken видит его на следующем `_check_token` (между каждым yield), а финальная проверка в `__exit__` ловит окно между последним yield и выходом из контекста. То есть «best-effort» **не** относится к нашей реализации — наша логика опроса не теряет события.
- TC — **строго one-shot lifecycle**: `_active: None → True → False`. Невалидные переходы кидают `TransactionAlreadyActiveError` (попытка `_start()` на активном TC) или `TransactionInactiveError` (любое использование TC, который ещё не стартовал ИЛИ уже остановлен — обе ситуации **функционально эквивалентны для пользователя**: TC не работает, нужно создать новый; различаются только текстом сообщения для удобства диагностики).
- Методы lifecycle `_start()` и `_stop()` — оба приватные по конвенции (lead-`_`). **Production code-path**: `_start()` — только из `__enter__`; `_stop()` — из `__exit__` и из `_stop_safely_at_exit` (atexit-hook при bypass `with`-блока). Прямой вызов `_start()`/`_stop()` корректно работает (escape-hatch, см. секцию «Подход»), но это **не публичный API** — нет гарантий стабильности сигнатуры между минорными версиями. **Тесты-affordance**: тесты могут вызывать `tc._start()` / `tc._stop()` напрямую для проверки lifecycle-инвариантов.
- Транзакция остановлена во время активной итерации (главный поток вышел из `with` до завершения worker-потоков) → workers получают `TransactionStoppedDuringIterationError` (fail-fast вместо silent corruption).
- Отсутствие `watchdog` → `WatchdogNotInstalledError(DirstreeError, ImportError)` при вызове `_start()` (или внутри `apply(transactional=True)`). Сам по себе доступ к `crawler.transaction` watchdog не загружает.
- При изменении в наблюдаемом scope → `TransactionalChangeError` с сообщением `Directory changed during transactional iteration: <event_type> at <path>` (для `moved` — `... moved from <path> to <dest>`). Структурированных полей нет, вся диагностика в тексте (тесты ассертят через `match=match(...)`). **Best-effort**: `ConditionToken` опрашивается на каждом `_check_token` (между каждым yield), поэтому обнаружение происходит на **первом же** yield'е после доставки события из ОС в наш handler. «Best-effort» относится к **OS-side latency окну** (платформенная задержка между мутацией и доставкой события watchdog'у — секции «Известные ограничения», п.1), не к нашей логике опроса. Тест `test_mutation_during_last_element_is_caught_through_both_iteration_and_apply` локирует, что окно покрыто **двумя** проверками: (a) post-loop проверкой в самом `go()` (после нормального exhaustion'а `source.go()`, перед StopIteration), которая ловит большинство случаев; и (b) финальной проверкой в `__exit__` для случаев, когда событие пришло после возврата из `go()` но до выхода из `with`-блока.

## Реализация ключевых элементов

**Замечание о тайп-хинтах и импортах в код-скелетах**: тайп-хинты показаны частично (там, где помогают понять контракт); импорты показаны только новые (atexit, functools, threading, warnings, typing, printo, ChangeAlarm). Остальные имена (`DefaultToken`, `CancellationError`, `ConditionToken`, `PossibleCallMatcher`, `AbstractCrawler`, исключения и т.п.) используются «как известные» из существующего кодбейса. **В реальной имплементации** все аннотации и импорты обязаны быть полными — иначе `mypy --strict` (CI gate) не пройдёт.

(*Заметка про язык кода — английский — вынесена в раздел «Принятые решения» в начале плана.*)

### `AbstractCrawler.transaction` и `apply`

```python
class AbstractCrawler:
    @property
    def transaction(self) -> 'TransactionalCrawler':
        from dirstree.crawlers.transactional.crawler import TransactionalCrawler  # avoid circular import
        return TransactionalCrawler(self)

    def apply(self, function, token=DefaultToken(), transactional=False):
        PossibleCallMatcher('.').match(function, raise_exception=True)
        if transactional:
            with self.transaction as scope:
                for path in scope.go(token):
                    function(path)
        else:
            for path in self.go(token):
                function(path)
```

Простые две ветки. `transactional=True` поднимает свежий TC через context manager. Дублирование двухстрочного цикла — приемлемая цена за отсутствие ветвлений с подменой переменной и manual start/stop.

### `TransactionalCrawler`

**Ключевые элементы дизайна**:

1. **Three-state `_active`**: `None` (fresh) → `True` (active, via `_start()`) → `False` (stopped, via `_stop()`, terminal). Любой метод (`_start()`/`_stop()`/`go()`/`apply()`), вызванный из не-разрешённого состояния, кидает соответствующее исключение.
2. **`_lifecycle_lock: threading.Lock`** — отдельный lock на TC, защищающий **проверку и переход `_active`** в методах `_start()`/`_stop()` плюс вызовы `_observer.start()` / `_observer.stop()` (на watchdog-уровне). `_observer.join()` намеренно вынесен **за пределы lock'а** (защита от deadlock при зависшем observer-потоке — см. docstring у `_stop()`). Не путать с `_change_alarm._lock` (тот защищает запись first-event внутри ChangeAlarm).
3. **`_load_watchdog`** — единственная точка import'а watchdog в пакете. Через `@staticmethod @functools.lru_cache(maxsize=None)` кешируется на уровне процесса. `FilteredEventHandler` определяется обычным `class` block **внутри тела метода** (в closure) и возвращается оттуда — никакого module-top импорта watchdog в пакете.
4. **`_change_alarm` и `_observer` — `cached_property`** на TC (по одному экземпляру на TC, лениво создаются).
5. **TC.apply переопределён**: default `transactional=True`, raises `InvalidTransactionalArgumentError` на `transactional=False`. Использует существующий observer (не создаёт новый).

```python
import atexit
import functools
import threading
import warnings
from typing import Optional
from printo import describe_data_object, not_none

from dirstree.crawlers.transactional.alarm import ChangeAlarm  # alarm.py — отдельный модуль (см. «Структура файлов»)
from functools import cached_property

class TransactionalCrawler(AbstractCrawler):
    def __init__(self, source: AbstractCrawler, active: Optional[bool] = None) -> None:
        # source: supported types — Crawler, PythonCrawler, CrawlersGroup. Other
        # AbstractCrawler subclasses raise TypeError inside `_observer`'s `unwrap_to_crawlers`
        # (see relevant section), covered by test `test_unsupported_abstract_crawler_subclass_raises_type_error`.
        #
        # active: present in the signature ONLY for `__repr__` representativeness — so that
        # `repr(tc)` for a started TC reads "TransactionalCrawler(source, active=True)".
        # External construction with `active is not None` is REJECTED at runtime: TC is not
        # part of the public API (created exclusively via `Crawler.transaction`), and there's
        # no legitimate caller passing this kwarg explicitly.
        if active is not None:
            raise RuntimeError(
                'The `active` argument is reserved for `__repr__` only. '
                'TransactionalCrawler instances are created automatically via `source.transaction`. '
                'Do not construct one directly.'
            )
        self.source = source
        self._active = active
        self._lifecycle_lock = threading.Lock()

    def __repr__(self) -> str:
        return describe_data_object(
            self.__class__.__name__,
            (self.source,),
            {'active': self._active},
            kwargs_filters={'active': not_none},
        )

    def __enter__(self) -> 'TransactionalCrawler':
        self._start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self._stop()
        # Final check AFTER drain — catches events delivered during shutdown window
        # (e.g. mutation right before exit, event still in dispatcher queue when go() finished).
        if exc_type is None and self._change_alarm.is_alarmed():
            raise TransactionalChangeError(self._change_alarm.get_message())

    @staticmethod
    @functools.lru_cache(maxsize=None)
    def _load_watchdog():
        try:
            from watchdog.observers import Observer
            from watchdog.events import FileSystemEventHandler
        except ImportError as exc:
            raise WatchdogNotInstalledError(
                'watchdog is not installed. Install it with: pip install dirstree[transactional]'
            ) from exc

        class FilteredEventHandler(FileSystemEventHandler):
            def __init__(self, source, change_alarm):
                super().__init__()
                self._source = source
                self._change_alarm = change_alarm
                self._excludes_spec = source._compile_excludes()
            def on_any_event(self, event):
                ...                                          # См. «ChangeAlarm и FilteredEventHandler» ниже.

        return Observer, FilteredEventHandler

    @property
    def transaction(self) -> 'TransactionalCrawler':
        # Override: TC is already a TC. Idempotent — return self if active.
        if self._active is not True:
            raise TransactionInactiveError(
                'Cannot start a new transaction from an inactive TransactionalCrawler. '
                'TransactionalCrawler objects are single-use; create a new one via original_crawler.transaction.'
            )
        return self

    @cached_property
    def _change_alarm(self) -> 'ChangeAlarm':
        """Lazily-created ChangeAlarm. Single shared instance per TC.

        THREAD-SAFETY: `functools.cached_property` is NOT thread-safe in Python <= 3.12 —
        two concurrent first-accesses may construct two instances and lose one. Python 3.13+
        added proper locking inside cached_property itself (see CPython issue gh-87634),
        but the project supports 3.8+, so we MUST behave correctly on the unsafe versions.
        We avoid the race by ensuring the first access ALWAYS happens inside `_start()` under
        `self._lifecycle_lock`. On 3.13+ this lock is technically redundant for cached_property
        protection (cached_property would self-serialize), but harmless and required for 3.8–3.12.
        Do not access this attribute from code paths that may run before `_start()` or outside
        the lifecycle lock on any supported Python version.
        """
        return ChangeAlarm()

    @cached_property
    def _observer(self):
        """Lazily creates Observer, walks source to leaf Crawlers, attaches one handler per leaf.

        THREAD-SAFETY: same caveat as `_change_alarm` (cached_property not thread-safe
        in Python <= 3.12; protected by `_lifecycle_lock` on all versions). First access must
        happen inside `_start()` under `_lifecycle_lock`."""

        # Local import: avoids circular dependency (`transactional → group → abstract → transactional`).
        from dirstree.crawlers.group import CrawlersGroup
        from dirstree.crawlers.crawler import Crawler

        def unwrap_to_crawlers(source):
            if isinstance(source, CrawlersGroup):
                for child in source.crawlers:
                    yield from unwrap_to_crawlers(child)
            elif isinstance(source, TransactionalCrawler):
                yield from unwrap_to_crawlers(source.source)
            elif isinstance(source, Crawler):
                yield source
            else:
                # Custom AbstractCrawler subclass without _compile_excludes / .extensions / etc.
                # Cannot construct a meaningful FilteredEventHandler for it.
                raise TypeError(
                    f'Unsupported source type {type(source).__name__!r} for TransactionalCrawler: '
                    f'expected Crawler, CrawlersGroup, or TransactionalCrawler subclass.'
                )

        Observer, FilteredEventHandler = self._load_watchdog()
        observer = Observer()
        observer.daemon = True                              # allow interpreter exit if __exit__ bypassed (rationale in _start() docstring)
        for leaf in unwrap_to_crawlers(self.source):
            handler = FilteredEventHandler(leaf, self._change_alarm)
            for base in leaf.paths:
                observer.schedule(handler, str(base), recursive=True)
        return observer

    def _start(self) -> None:
        with self._lifecycle_lock:
            if self._active is True:
                raise TransactionAlreadyActiveError('TransactionalCrawler is already active.')
            if self._active is False:
                raise TransactionInactiveError(
                    'TransactionalCrawler has already been stopped and cannot be restarted. '
                    'Create a new one via original_crawler.transaction.'
                )
            # Transition to active: cached_property triggers Observer setup + .start() under lock.
            self._observer.start()
            self._active = True
            # atexit fallback: if interpreter shuts down while transaction is still active
            # (caller forgot to leave `with` block), force _stop() so observer is cleaned up
            # at exit. The bound method instance is registered; deregistration happens in _stop().
            #
            # atexit and daemon=True work at DIFFERENT shutdown layers, not as duplicate safeguards:
            #   * atexit = graceful-bypass cleanup. When interpreter shuts down normally
            #     (sys.exit, falling off main) and __exit__ was skipped, atexit runs _stop()
            #     so watchdog releases inotify/FSEvents handles and emits diagnostic warnings.
            #   * daemon=True = hard floor. If atexit itself hangs or is bypassed (os._exit,
            #     SIGKILL), the watchdog thread dies with the process instead of pinning it.
            atexit.register(self._stop_safely_at_exit)

    def _stop(self) -> None:
        with self._lifecycle_lock:
            if self._active is None:
                raise TransactionInactiveError(
                    'Cannot stop a TransactionalCrawler that has never been started.'
                )
            if self._active is False:
                raise TransactionInactiveError('TransactionalCrawler has already been stopped.')
            self._observer.stop()
            observer_ref = self._observer
            self._active = False
        # Normal path: deregister atexit hook (already cleaned up; unregister is no-op if absent).
        atexit.unregister(self._stop_safely_at_exit)
        # join() outside lock — avoids deadlock if observer hangs; 5s timeout protects against FSEvents stalls on macOS.
        observer_ref.join(timeout=5.0)
        if observer_ref.is_alive():
            warnings.warn(
                'TransactionalCrawler observer thread did not terminate within 5s of stop(). '
                'This is a watchdog/OS-layer bug; the thread is being left as a daemon.',
                ResourceWarning,
                stacklevel=2,
            )

    def go(self, token=DefaultToken()):
        if self._active is not True:
            raise TransactionInactiveError(
                'TransactionalCrawler can only be used inside an active transaction. '
                'Use `with crawler.transaction as scope:` and work with `scope` inside the block.'
            )
        # cond_alive / cond_no_alarm — see "Подход" section for rationale (identity-based
        # disambiguation via `exc.token is cond_alive` / `exc.token is cond_no_alarm`).
        # Composition: source.go(token + cond_alive + cond_no_alarm).
        cond_alive = ConditionToken(lambda: self._active is True)
        cond_no_alarm = ConditionToken(lambda: not self._change_alarm.is_alarmed())
        try:
            for path in self.source.go(token + cond_alive + cond_no_alarm):
                yield path
        except Exception as exc:
            # Extract CancellationError directly or via __cause__ (custom source.raise_on_cancel case).
            cancellation = exc if isinstance(exc, CancellationError) else (
                exc.__cause__ if isinstance(exc.__cause__, CancellationError) else None
            )
            if cancellation is None:
                raise
            if cancellation.token is cond_alive:
                raise TransactionStoppedDuringIterationError(
                    'Transaction was stopped while iteration was in progress.'
                ) from None
            if cancellation.token is cond_no_alarm:
                raise TransactionalChangeError(self._change_alarm.get_message()) from None
            raise                                            # user-token cancellation, propagate as-is
        # Final check after clean source exhaustion (covers source.raise_on_cancel=False case).
        if self._change_alarm.is_alarmed():
            raise TransactionalChangeError(self._change_alarm.get_message())
        if self._active is not True:
            raise TransactionStoppedDuringIterationError(
                'Transaction was stopped while iteration was in progress.'
            )

    def _stop_safely_at_exit(self) -> None:
        # Called from atexit if `__exit__` was bypassed (e.g. user called sys.exit() before
        # leaving the with-block, or transaction is reachable garbage at interpreter shutdown).
        # MUST be exception-safe — atexit handlers that propagate exceptions can corrupt the
        # rest of the atexit chain. But silent suppression hides real bugs (race conditions,
        # watchdog failures). Compromise: catch Exception and emit a ResourceWarning with
        # diagnostic detail — symmetric with the existing warn in `_stop()` above. stderr may
        # be closed at very late shutdown, in which case the warning is lost; that's acceptable.
        try:
            if self._active is True:
                self._stop()
        except Exception as exc:
            warnings.warn(
                f'TransactionalCrawler._stop_safely_at_exit caught an unexpected exception '
                f'while cleaning up at interpreter shutdown: {exc!r}',
                ResourceWarning,
                stacklevel=2,
            )

    def apply(self, function, token=DefaultToken(), transactional=True):
        # Override: transactional=False is invalid for TC (kwarg kept for signature compatibility).
        if transactional is False:
            raise InvalidTransactionalArgumentError(
                'Cannot pass transactional=False to TransactionalCrawler.apply(). '
                'TransactionalCrawler is always transactional; this kwarg is kept only for '
                'signature compatibility with non-transactional crawlers.'
            )
        PossibleCallMatcher('.').match(function, raise_exception=True)
        for path in self.go(token):
            function(path)
```

`__iter__` уже определён в `AbstractCrawler` через `self.go()` → проверка активности произойдёт при первом `next()`. `apply()` переопределён (см. выше) — использует существующий observer через `self.go()`, новый Observer не создаётся.

**Lifecycle cached_property**: `_observer` и `_change_alarm` создаются на первом обращении (всегда из `_start()` под `_lifecycle_lock`). После `_stop()` они остаются в `self.__dict__`, и **публичные методы итерации** (`go`, `__iter__`, `apply`) при `_active=False` кидают `TransactionInactiveError`. **Исключения** для внутреннего использования: `__exit__` после `_stop()` ещё читает `_change_alarm.is_alarmed()` / `.get_message()` для final-check на late events (это intentional, см. секцию «Гарантии»); `_stop_safely_at_exit` (atexit-hook) обращается к `_active` и `_observer` для cleanup'а. Эти пути безопасны: `_change_alarm` к моменту `__exit__` уже cached в `self.__dict__`, обычный atomic dict-lookup, без race на cached_property body.

### `ChangeAlarm` и `FilteredEventHandler`

`ChangeAlarm` — единый thread-safe holder. Содержит `threading.Lock` (для атомарной записи первого события), `threading.Event` (сигнал для ConditionToken), `message: str` (готовая строка сообщения, формируется при `set_first`).

Скелет:
```python
class ChangeAlarm:
    def __init__(self):
        self._lock = threading.Lock()
        self._event = threading.Event()
        self.__message = ''

    def set_first(self, path, event_type, dest_path=None):
        with self._lock:
            if self._event.is_set():
                return
            if dest_path is None:
                message = f'Directory changed during transactional iteration: {event_type} at {path}'
            else:
                message = f'Directory changed during transactional iteration: {event_type} from {path} to {dest_path}'
            self._do_record(message)

    def _do_record(self, message):
        # Atomic block: record message and set event. Extracted as a separate private method
        # solely for testability via locklib.LockTraceWrapper (see test
        # `test_change_alarm_set_first_writes_data_under_lock`).
        self.__message = message
        self._event.set()

    def get_message(self) -> str:
        # Read under lock — provides acquire barrier matching the release barrier
        # implicit in `with self._lock` on the write side. Needed for free-threading
        # 3.13t/3.14t where plain attribute reads have no happens-before edge
        # with respect to threading.Event.set()-side writes. On GIL-CPython this is
        # redundant (GIL provides full barriers) but cheap.
        with self._lock:
            return self.__message

    def is_alarmed(self) -> bool:
        # Lock-free read of threading.Event._flag. Intentionally NOT under self._lock —
        # this method is polled by ConditionToken at every yield in source.go(); taking a
        # lock per yield would noticeably slow down the iteration hot path.
        #
        # Why this is safe even on free-threading (3.13t / 3.14t):
        # 1. `threading.Event.set()` acquires/releases the Event's internal `_cond` lock,
        #    which provides release semantics for `_flag`. A reader without acquiring
        #    `_cond` has NO acquire pair → may briefly see `_flag=False` after writer set
        #    it. This is fine: ConditionToken polls again on the next yield, so the alarm
        #    is detected at most O(1) yields late.
        # 2. The *message* visibility — the critical correctness property — is NOT tied to
        #    Event semantics at all. `__message` is written under `self._lock` and read in
        #    `get_message()` under the same `self._lock` (separate lock from Event's `_cond`).
        #    So once a reader sees `is_set()=True` and calls `get_message()`, the lock
        #    acquire pairs with the writer's release of `self._lock` in `_do_record()` and
        #    guarantees the freshest message. The lock in `get_message()` is necessary
        #    precisely because Event semantics alone do NOT cover `__message`.
        return self._event.is_set()

    def wait(self, timeout) -> bool:
        return self._event.wait(timeout)
```

Публичный API `ChangeAlarm`:
- `set_first(path, event_type, dest_path=None)` — под `Lock`: если ещё не сработало, делегирует в приватный `_do_record(message)`, который атомарно сохраняет сообщение и выставляет Event. **Зачем split на `set_first` (берёт lock) и `_do_record` (выполняет запись)**: единственная причина — тестируемость через `locklib.LockTraceWrapper`. Чтобы доказать, что запись действительно происходит под удержанным lock'ом, тест оборачивает `_do_record` своей callable, которая делает `lock.notify('write')` перед делегированием. Потом проверяется `was_event_locked('write')`. Если бы `set_first` была монолитным методом без точки extension — внедрить notify без модификации production-кода было бы нельзя. Прямой вызов `_do_record` пользователем — нарушение API; внешне доступен только `set_first`.
- `is_alarmed() -> bool` — `self._event.is_set()`, без блокировки.
- `wait(timeout)` — `self._event.wait(timeout)`, для тестов.
- `get_message() -> str` — берёт `_lock` и возвращает текущее сообщение. Использование lock'а даёт reader-side acquire barrier (нужен на free-threading 3.13t/3.14t, где plain attribute read не имеет happens-before-связи с `threading.Event.set()`-side writes). До первого `set_first` возвращает пустую строку.
- `__message: str` — внутренний атрибут (**name-mangled** в `_ChangeAlarm__message`). Прямой доступ извне через `alarm._message` → `AttributeError` (защита от случайного чтения, которое сломало бы visibility-контракт на free-threading; см. visibility-caveat в `is_alarmed()` docstring). Тестам, которым **действительно** нужно инспектировать значение в обход `get_message()`, нужно использовать mangled-имя `alarm._ChangeAlarm__message` — это явный сигнал, что доступ нестандартный.

`TransactionalChangeError(trip_state.get_message())` создаётся прямо в `go()` — без отдельного фабричного метода на `ChangeAlarm`.

`FilteredEventHandler` — наследник watchdog'овского `FileSystemEventHandler`. `__init__` принимает `source: Crawler` и `ChangeAlarm`, единожды собирает `excludes_spec = source._compile_excludes()` и держит ссылки на `extensions`, `filter`, `only_files`, watched base paths.

В `on_any_event(event)` (вызывается фоновым потоком watchdog для каждого события). Принимает `event: watchdog.events.FileSystemEvent`, у которого есть атрибуты `event.event_type: str` (`'created'` / `'deleted'` / `'modified'` / `'moved'` — это именно те строки, которые попадут в `ChangeAlarm.message`), `event.src_path: str`, `event.is_directory: bool`, и для moved — `event.dest_path: str`. Логика:
1. Если `event.is_directory` и `self._source.only_files=True` → ignore (file-event придёт следом).
2. Нормализовать `event.src_path` → `Path` (абсолютный).
3. Проверка exclude: применить `excludes_spec.match_file(str(path))` (`pathspec.match_file` принимает строку, не `Path`); для dir-event дополнительно `excludes_spec.match_file(f'{path}/')` (как делает `Crawler._traverse` в `crawler.py:157`). Никакого parent-walk — для строгой behavioral parity с `_traverse`. Это значит, например, что `exclude=['build/']` отфильтрует только сам `build/` (директорию), но не `build/x.txt` (это уже свойство `pathspec`+`_traverse`-логики, а не наша забота — handler должен зеркалить `_traverse`).
4. Для `self._source.extensions` (только file-events) — проверить `path.suffix`.
5. Если `self._source.filter is not None` — вызвать. Исключения из user-filter перехватить через `except Exception` (**не** `except BaseException` — `KeyboardInterrupt`/`SystemExit` не должны перехватываться) и считать trip-ом (безопасный дефолт; этот путь специально протестируем).
6. Для `moved` — то же самое для `dest_path`. Trip если хоть один путь проходит.
7. Если фильтры пройдены — `self._change_alarm.set_first(path, event.event_type, dest_path)`. Под Lock'ом внутри — если уже сработало, новое игнорируется (first-wins).

### Исключения

```python
class TransactionalChangeError(DirstreeError):
    pass

class TransactionInactiveError(DirstreeError):
    pass

class TransactionAlreadyActiveError(DirstreeError):
    pass

class TransactionStoppedDuringIterationError(DirstreeError):
    pass

class InvalidTransactionalArgumentError(DirstreeError):
    pass

class WatchdogNotInstalledError(DirstreeError, ImportError):
    pass
```

Все шесть минималистичны, по стилю существующего `IncompatibleCrawlerOptionsError`. Никаких custom `__init__`, никаких структурированных полей. Точные тексты сообщений — в код-скелетах выше.

Два сознательных решения:

- `TransactionalChangeError` НЕ наследник `cantok.CancellationError`. Причина: пользователи могут писать `except CancellationError: pass` для тихой обработки отмены — и в этом случае «директория изменилась» проглотилось бы тихо. Перевод `CancellationError → TransactionalChangeError` делает сам `TransactionalCrawler.go`.
- `WatchdogNotInstalledError(DirstreeError, ImportError)` — multiple inheritance с `ImportError`, чтобы и `except ImportError` отлавливал.

## Взаимодействие с существующими опциями

- **`freeze=True`** (существующий параметр `Crawler` — см. `crawler.py`, заранее собирает snapshot всех путей через `list(_traverse(...))` перед итерацией): Observer стартует ДО первого `go()`. При `freeze=True` источник сначала строит snapshot, потом отдаёт пути — оба прохода защищены условным токеном. Корректно работает.
- **`raise_on_cancel`**: TC различает три источника отмены через identity внутренних condition-токенов (`cond_alive`, `cond_no_alarm`):
  - Срабатывание `cond_no_alarm` (изменение в директории) → `TransactionalChangeError`. **Всегда**, независимо от `source.raise_on_cancel`.
  - Срабатывание `cond_alive` (транзакция остановлена во время итерации) → `TransactionStoppedDuringIterationError`. **Всегда**.
  - Любая другая отмена (от user-токена, переданного в `go(token)`, или от `source.token`) — пробрасывается как есть, в том типе, который её поднял (`CancellationError` или кастомное исключение из `raise_on_cancel=...`).
  - Принадлежность исключения конкретному токену проверяем через `is`-сравнение `exception.token is cond_alive` / `exception.token is cond_no_alarm`. Если `source.raise_on_cancel` — кастомное исключение, cantok оборачивает оригинальный `CancellationError` через `raise X from original`, и мы достаём его через `exc.__cause__`.
- **`CrawlersGroup`**: поддерживается централизованно. `group.transaction` возвращает `TransactionalCrawler(group)` без рекурсивного оборачивания. В cached_property `_observer` внутренний хелпер `unwrap_to_crawlers(source)` разворачивает группу до листовых Crawler-ов, создаёт **отдельный handler** на каждый листовой Crawler (со своими фильтрами), привязывает к **одному Observer**, использует **один shared `ChangeAlarm`**. Cross-child trip обнаруживается автоматически — любое событие в любом листе пишет в shared ChangeAlarm. Watchdog сам дедуплицирует OS-подписки для одинаковых путей.
- **Вложенный TC внутри group** (например, `Crawler('a').transaction + Crawler('b')`): внутренний `unwrap_to_crawlers()` транзитивно разворачивает TC до underlying Crawler. Это означает дублирование наблюдения, если внешний TC уже активен (его собственный Observer + наш). Документируется как не-рекомендуемый сценарий; при типичном использовании (`(c1 + c2).transaction`) проблемы нет.
- **Несколько `paths` в одном Crawler**: один Observer, несколько `schedule()` с `recursive=True`.
- **Внешний `token`**: передаваемый снаружи в `scope.go(token)` или `scope.apply(fn, token=...)` композируется с нашим `ConditionToken` через `+` и продолжает работать (можно отменить итерацию извне).

## TDD-стратегия

Состав тестов: **83 core-теста** транзакционной логики (нумерация 1-83, ниже), плюс **3 stress-теста** (CPU pressure, в том же файле под маркером `@pytest.mark.stress`), плюс **Python invariant tests** (~8 тестов в `tests/python/interpreter/` и `tests/python/dependencies/`, см. секцию «Python invariant tests» в конце).

Основной сьют — `tests/units/crawlers/transactional/test_crawler.py` (зеркало `dirstree/crawlers/transactional/crawler.py`). Соблюдаем конвенции репо: тесты — функции (не классы), у каждого docstring (первая строка — property, дальше — почему/как), никаких module-level хелперов (всё внутри теста), `tmp_path` для мутирующих сценариев, `pytest.raises(..., match=match('точное'))` через `from full_match import match`, `pytest.mark.skipif` только на уровне декоратора. Синхронизация — `threading.Barrier` для детерминированного rendezvous и `_change_alarm.wait(timeout=2.0)` (или `_change_alarm._event.wait(...)` напрямую) для ожидания асинхронной доставки. Никогда не голый `sleep`. Доступ к `_change_alarm`, `_observer`, `_active` — приватные test-affordance, документируется в комментарии у поля.

**Кроме unit-сьюта** — отдельный файл `tests/python/interpreter/test_atomicity.py` с тестом инварианта Python (см. ниже секцию «Python invariant tests»). Не относится к нашей имплементации напрямую — проверяет фундаментальное свойство Python, на которое мы опираемся в `cond_alive` (read of `self._active`).

**Test infrastructure (`wait()` + sentinel-pattern).**

**`_change_alarm.wait(timeout=2.0)`** — `threading.Event.wait(timeout) -> bool`. Блокирует поток до `_event.set()` или таймаута; возвращает `True`/`False`. Используется для **детерминированного** дожидания асинхронной цепочки `мутация → watchdog handler → ChangeAlarm.set_first` (без `time.sleep`). Timeout `2.0` — safety-net против зависания. **Возврат всегда проверяется ассертом** (`assert scope._change_alarm.wait(timeout=2.0)`) — иначе на timeout-сценарии последующие assertions могут давать false positive на пустой `message`. В docstring каждого теста, использующего `wait()`, обязательно описать *что именно* ждём.

**Sentinel-pattern** — для детерминированной проверки «событие X **не** trip'нуло, потому что было отфильтровано» (без хрупкого `sleep`). Worker через `Barrier` сначала trigger'ит **ignored**-событие (в filtered scope), потом сразу **sentinel**-событие (в un-filtered scope). Главный поток `assert scope._change_alarm.wait(timeout=2.0)` — обязательно дождётся sentinel'а. Проверка: `scope._change_alarm.get_message()` содержит sentinel, **не** ignored (если бы ignored trip'нул, first-wins зафиксировал бы его до sentinel'а). Допущение: `watchdog.Observer` имеет один dispatcher-поток, события из emitter'а обрабатываются последовательно — инвариант покрыт `test_observer_single_dispatcher_processes_events_sequentially` (см. impact-описание там же).

**Кэш `_load_watchdog` между тестами.** `_load_watchdog` — `@functools.lru_cache(maxsize=None)`, кэш живёт на уровне процесса. Тесты, которые `monkeypatch.setattr(TransactionalCrawler, '_load_watchdog', ...)`, **должны** сбрасывать кэш до подмены: `TransactionalCrawler._load_watchdog.cache_clear()`. Иначе риск: предыдущий тест закешировал реальные классы, текущий тест увидит их вместо своих обёрнутых. Рекомендуется обернуть в pytest autouse fixture в `conftest.py`:
```python
@pytest.fixture(autouse=True)
def _clear_load_watchdog_cache():
    # ВАЖНО: capture оригинального lru_cache-объекта ДО теста.
    # Внутри теста monkeypatch может подменить TransactionalCrawler._load_watchdog
    # на staticmethod(lambda: ...) — у lambda нет .cache_clear(). Если бы мы читали
    # атрибут в teardown'е (после yield), могли бы попасть на ещё не отозванный
    # monkeypatch (зависит от teardown ordering) и упасть с AttributeError.
    # Захват ссылки до теста делает фикстуру независимой от ordering.
    original = TransactionalCrawler._load_watchdog
    original.cache_clear()
    yield
    original.cache_clear()
```

**Instance-level monkeypatch методов (важно — Python gotcha).** В нескольких тестах (`test_first_event_wins_subsequent_changes_do_not_overwrite_message`, `test_change_alarm_set_first_writes_data_under_lock`) мы делаем `instance.method = spy`. Это **присваивание атрибута инстанса**, а не подмена метода класса. Последствие: при вызове `instance.method(arg)` Python НЕ биндит `self` (потому что атрибут найден в `__dict__` инстанса до descriptor protocol). Следовательно, **`spy` должна быть написана БЕЗ `self`-параметра**:
```python
def spy(path, event_type, dest_path=None):  # NO self! Will be called bare.
    ...
instance._change_alarm.set_first = spy
```
Это intentional — мы хотим спай-обёртку, не override метода класса. Если кто-то ошибочно напишет `def spy(self, path, ...)` — упадёт с `TypeError: missing 1 required positional argument`. Альтернатива (если хочется явно): `types.MethodType(spy_with_self, instance)`. Мы выбираем первый вариант для краткости — но в plans/imp обязательно явно отмечать «no self».

Каждый тест ниже: **имя → property → как проверяется**. Тесты сгруппированы по темам (lifecycle / event detection / filtering / threading / apply / errors / cancellation / group / repr / cross-source / shutdown). Внутри группы примерный порядок — от базовых к производным; точный red→green-порядок имплементации остаётся на усмотрение разработчика (часть тестов внутри одной группы требует одних и тех же production-helper'ов).

### Базовая корректность

**1. `test_transactional_yield_set_is_identical_to_plain_crawl_when_tree_is_static_for_all_source_types`**
- *Property:* при отсутствии изменений `TransactionalCrawler` выдаёт ровно тот же **набор путей** (yield-set equivalence), что обычный обход на том же дереве — для всех трёх типов source: `Crawler`, `PythonCrawler`, `CrawlersGroup`. Это покрывает базовый «source может быть любым из них; все они поддерживаются через .go()/.paths/etc». **Не покрывает**: structural инварианты CrawlersGroup (один Observer + N handler'ов + shared ChangeAlarm) — это локирует `test_group_transaction_uses_single_observer_with_per_leaf_handlers_sharing_one_change_alarm`; filter-isolation между leaf'ами — `test_overlapping_paths_in_group_use_distinct_handlers_with_independent_filters`.
- *Как:* inner for-loop по source-сценариям:
  ```python
  # tmp_path заполнен: a.py, b.py, c.txt, sub/d.py, sub/e.txt
  scenarios = [
      ('Crawler',         lambda: Crawler(tmp_path)),
      ('PythonCrawler',   lambda: PythonCrawler(tmp_path)),
      ('CrawlersGroup',   lambda: Crawler(tmp_path / 'sub') + Crawler(tmp_path, exclude=['sub/**'])),
  ]
  for source_name, build_source in scenarios:
      source = build_source()
      expected = sorted(source.go())
      with source.transaction as scope:
          actual = sorted(scope)
      assert actual == expected, f'mismatch for {source_name}'
  ```
  Покрывает: (а) Crawler — базовый случай, (б) PythonCrawler — наследник с переопределёнными extensions, (в) CrawlersGroup — `unwrap_to_crawlers` корректно разворачивает группу и каждый лист участвует в централизованной схеме. **Замечание про CrawlersGroup**: конструкция `Crawler(sub) + Crawler(tmp_path, exclude=['sub/**'])` — это **complementary partition** дерева (первый лист видит только `sub/*`, второй — всё остальное); union через `CrawlersGroup.go()` даёт тот же yield-set, что и одиночный `Crawler(tmp_path)`. Тест ограничивается этой статической эквивалентностью; structural инварианты группы (N handler'ов с разными фильтрами) покрыты отдельными group-тестами.

**2. `test_transaction_property_returns_fresh_instance_each_access`**
- *Property:* `crawler.transaction` каждый раз создаёт новый `TransactionalCrawler`, не кэширует. Это инвариант, обеспечивающий thread-safety.
- *Как:* `first = crawler.transaction; second = crawler.transaction`. Проверить `first is not second`, `isinstance(first, TransactionalCrawler)`, `first.source is crawler`, `first._active is None` (fresh — never started).

**3. `test_enter_exit_lifecycle_through_all_scenarios`**
- *Property:* Контекст-менеджер полностью покрыт: (a) `__enter__` возвращает self и активирует TC с живым Observer; (b) нормальный `__exit__` останавливает Observer, переводит в `_active=False`, возвращает None; (c) исключение из тела `with` (как до итерации, так и в середине) пробрасывается наружу, но Observer всё равно остановлен.
- *Как:* inner for-loop по четырём сценариям:
  ```python
  scenarios = [
      ('noop',                 lambda scope: None,              None),       # __enter__ + clean __exit__
      ('raise_after_enter',    lambda scope: None,              RuntimeError('boom')),  # body fn — noop; raise сразу после __enter__
      ('raise_mid_iter',       lambda scope: next(iter(scope)), RuntimeError('boom')),  # consume один элемент, потом raise
  ]
  for name, body_fn, exc_to_raise in scenarios:
      tc = crawler.transaction
      if exc_to_raise is None:
          with tc as scope:
              assert scope is tc
              assert scope._active is True
              assert scope._observer.is_alive()
              body_fn(scope)
          # после нормального выхода
          assert tc._active is False
          assert tc._observer.is_alive() is False
      else:
          with pytest.raises(type(exc_to_raise), match=match(str(exc_to_raise))):
              with tc as scope:
                  body_fn(scope)
                  raise exc_to_raise
          # после exception-выхода
          assert tc._active is False
          assert tc._observer.is_alive() is False
  ```

**4. `test_cached_properties_return_same_instance_on_multiple_accesses`**
- *Property:* `_observer` и `_change_alarm` — оба `cached_property`: повторные обращения возвращают тот же экземпляр (не создают новый Observer / ChangeAlarm).
- *Как:* inner for-loop по двум именам атрибутов:
  ```python
  for attr in ['_observer', '_change_alarm']:
      tc = crawler.transaction
      tc._start()
      first = getattr(tc, attr)
      second = getattr(tc, attr)
      assert first is second
      tc._stop()
  ```

**5. `test_observer_attribute_persists_after_stop_but_thread_is_dead`**
- *Property:* После `stop()` атрибут `_observer` сохраняется в `tc.__dict__` (cached_property не очищается), но сам поток Observer мёртв.
- *Как:* `tc = crawler.transaction; tc._start(); observer_ref = tc._observer; tc._stop(); assert tc._observer is observer_ref; assert observer_ref.is_alive() is False`.

### Inactive / lifecycle state

**6. `test_inactive_tc_iteration_methods_all_raise_inactive_error`**
- *Property:* Любой метод итерации (`go`, `__iter__`, `apply`) на свежем TC без `__enter__`/`_start()` кидает `TransactionInactiveError` с одинаковым (общим) текстом инструкции.
- *Как:* inner for-loop по сценариям:
  ```python
  expected = match('TransactionalCrawler can only be used inside an active transaction. Use `with crawler.transaction as scope:` and work with `scope` inside the block.')
  scenarios = [
      lambda tc: next(tc.go()),
      lambda tc: list(tc),
      lambda tc: tc.apply(lambda path: None),
  ]
  for operation in scenarios:
      tc = crawler.transaction
      with pytest.raises(TransactionInactiveError, match=expected):
          operation(tc)
  ```

**7. `test_reuse_scope_after_with_exit_raises_inactive_error`**
- *Property:* Сохранённая снаружи ссылка на `scope` после выхода из `with` нерабочая — гарантируем, что TC не оживает.
- *Как:* `with crawler.transaction as scope: pass`. После блока `with pytest.raises(TransactionInactiveError): list(scope)`.

**8. `test_lifecycle_invalid_transitions_raise_per_state`**
- *Property:* Невалидные lifecycle-переходы кидают конкретные исключения per-state. Покрывает все запрещённые комбинации `(initial_state, operation)`.
- *Как:* inner for-loop по сценариям `(setup_fn, operation, expected_exc, expected_msg)`:
  ```python
  scenarios = [
      # double start on active TC
      (lambda tc: tc._start(),                                # setup: active
       lambda tc: tc._start(),                                # operation
       TransactionAlreadyActiveError, 'TransactionalCrawler is already active.'),
      # start on stopped TC (one-shot)
      (lambda tc: (tc._start(), tc._stop()),                  # setup: stopped
       lambda tc: tc._start(),
       TransactionInactiveError, 'TransactionalCrawler has already been stopped and cannot be restarted. Create a new one via original_crawler.transaction.'),
      # stop on fresh TC
      (lambda tc: None,                                      # setup: fresh
       lambda tc: tc._stop(),
       TransactionInactiveError, 'Cannot stop a TransactionalCrawler that has never been started.'),
      # double stop on stopped TC
      (lambda tc: (tc._start(), tc._stop()),                  # setup: stopped
       lambda tc: tc._stop(),
       TransactionInactiveError, 'TransactionalCrawler has already been stopped.'),
  ]
  for setup, op, exc_cls, msg in scenarios:
      tc = crawler.transaction
      setup(tc)
      with pytest.raises(exc_cls, match=match(msg)):
          op(tc)
  ```

**9. `test_lifecycle_state_invariants_for_three_state_active_flag`**
- *Property:* `_active` принимает строго три значения с детерминированными переходами: `None` (fresh) → `True` (active via `_start()`) → `False` (stopped via `_stop()`). `False` — терминальное; после `pytest.raises` на failed restart `_active` остаётся `False`.
- *Как:*
  ```python
  tc = crawler.transaction
  assert tc._active is None
  tc._start();   assert tc._active is True
  tc._stop();   assert tc._active is False
  with pytest.raises(TransactionInactiveError): tc._start()
  assert tc._active is False  # terminal, no change
  ```

### Event detection / ChangeAlarm / lifecycle-lock

**10. `test_each_file_event_type_during_iteration_raises_with_correct_message`**
- *Property:* Каждый из четырёх типов file-event (`created`, `deleted`, `moved`, `modified`), случившийся между yield'ами, поднимает `TransactionalChangeError` с правильным `event_type` и путями в сообщении.
- *Как:* inner for-loop по сценариям. **Ordering для deleted/moved**: мутация делается **после** того, как `_traverse` уже стейтнул целевой файл — обход бага `Crawler._traverse` (см. `issue_self.md` и «Известные ограничения», п.2; когда баг исправят, ordering можно будет упростить). Каждый сценарий синхронизируется через `threading.Event` `yielded_target`, который filter ставит после прохождения целевого файла. Worker ждёт `yielded_target.wait(timeout=2.0)` перед мутацией; затем ждёт `_change_alarm.wait(timeout=2.0)` чтобы гарантировать доставку события (детерминирует тест на slow CI):
  ```python
  scenarios = [
      ('created',  'new.txt',      lambda tmp: (tmp/'new.txt').touch(),
                   'Directory changed during transactional iteration: created at {tmp}/new.txt'),
      ('deleted',  'existing.txt', lambda tmp: (tmp/'existing.txt').unlink(),
                   'Directory changed during transactional iteration: deleted at {tmp}/existing.txt'),
      ('moved',    'a.txt',        lambda tmp: (tmp/'a.txt').rename(tmp/'b.txt'),
                   'Directory changed during transactional iteration: moved from {tmp}/a.txt to {tmp}/b.txt'),
      ('modified', 'existing.txt', lambda tmp: (tmp/'existing.txt').write_text('changed'),
                   'Directory changed during transactional iteration: modified at {tmp}/existing.txt'),
  ]
  for event_name, target_name, mutation, expected in scenarios:
      # setup fresh tmp_path with target_name + a few non-target files (so iteration is non-trivial)
      yielded_target = threading.Event()
      def filter_(path):
          if path.name == target_name:
              yielded_target.set()        # signal worker: target passed _traverse's is_file()
          return True
      def worker():
          assert yielded_target.wait(timeout=2.0)
          mutation(tmp_path)
          assert scope._change_alarm.wait(timeout=2.0)   # ensure event delivery before main proceeds
      threading.Thread(target=worker).start()
      crawler = Crawler(tmp_path, filter=filter_)
      with pytest.raises(TransactionalChangeError, match=match(expected.format(tmp=tmp_path))):
          with crawler.transaction as scope:
              list(scope)
  ```
- **Без `skipif`** для `modified` (см. «Известные платформенные особенности» — `ChangeAlarm` first-wins иммунен к Windows-дубликатам). Если тест реально флакает — обращаемся к пользователю прежде чем добавлять skipif.

**11. `test_first_event_wins_subsequent_changes_do_not_overwrite_message`**
- *Property:* Integration-проверка first-wins через реальный watchdog. Сам контракт `ChangeAlarm.set_first` доказан unit-тестом `test_change_alarm_subsequent_set_first_does_not_overwrite_message` (прямые вызовы); здесь — end-to-end через две реальные ФС-мутации.
- *Как:* spy на `scope._change_alarm.set_first` (per-instance monkeypatch). Счётчик вызовов + `threading.Event`, ставящийся когда счётчик достигает 2:
  ```python
  call_count = [0]
  second_call_seen = threading.Event()
  original_set_first = scope._change_alarm.set_first
  def spy(path, event_type, dest_path=None):
      call_count[0] += 1
      result = original_set_first(path, event_type, dest_path)
      if call_count[0] >= 2:
          second_call_seen.set()
      return result
  scope._change_alarm.set_first = spy
  ```
  Сценарий (синхронизация: `threading.Barrier(2)` для двух rendezvous main↔worker, плюс два `threading.Event` для асинхронных дожиданий):
  ```python
  barrier_first = threading.Barrier(2)               # rendezvous before first touch
  barrier_second = threading.Barrier(2)              # rendezvous before second touch
  # second_call_seen — определён выше в spy-сетапе
  def worker():
      barrier_first.wait(timeout=2.0)
      (tmp_path/'first.txt').touch()
      barrier_second.wait(timeout=2.0)
      (tmp_path/'second.txt').touch()
  threading.Thread(target=worker).start()
  ```
  1. Main: `barrier_first.wait()` — синхронизация перед первой мутацией.
  2. Main: `assert scope._change_alarm.wait(timeout=2.0)` — дождались первого trip (`_event.is_set()`).
  3. Main: сохранить `first_message = scope._change_alarm.get_message()`.
  4. Main: `barrier_second.wait()` — синхронизация перед второй мутацией.
  5. Main: `assert second_call_seen.wait(timeout=2.0)` — дождались, что `set_first` был вызван **вторым** событием (и проигнорирован first-wins-логикой внутри).
  6. Main: `assert scope._change_alarm.get_message() == first_message` — message не перетёрто.
  7. Выход из with → `pytest.raises(TransactionalChangeError, match=match(...first.txt...))`.

**12. `test_change_alarm_subsequent_set_first_does_not_overwrite_message`**
- *Property:* Unit-тест на `ChangeAlarm.set_first` — повторный вызов после первого срабатывания не меняет уже записанное сообщение.
- *Как:* `alarm = ChangeAlarm(); alarm.set_first(Path('/a'), 'created'); first_msg = alarm.get_message(); alarm.set_first(Path('/b'), 'deleted'); assert alarm.get_message() == first_msg`.

**Шаблон LockTrace-тестов для lifecycle** (`test_start_state_transition_happens_under_lifecycle_lock`, `test_stop_state_transition_happens_under_lifecycle_lock`, `test_observer_join_happens_OUTSIDE_lifecycle_lock`, `test_observer_cached_property_first_access_under_lifecycle_lock`). Все используют одну инфраструктуру: monkeypatch `_load_watchdog` возвращает обёрнутый Observer-класс, который переопределяет нужный метод (`start`/`stop`/`join`); lifecycle_lock на TC оборачивается в `LockTraceWrapper`; обёртка делает `notify('event_name')`; после `tc._start()`/`tc._stop()` проверяем `was_event_locked(...)`. Полная boilerplate показана в `test_start_state_transition_happens_under_lifecycle_lock` ниже, остальные тесты переиспользуют её и отличаются только переопределённым методом + именем event'а.

**13. `test_start_state_transition_happens_under_lifecycle_lock`**
- *Property:* В `TC._start()` вызов `self._observer.start()` (запуск watchdog-потока) происходит **под удержанным `_lifecycle_lock`**. Без lock'а два потока могли бы оба пройти проверку `_active is None` и поднять watchdog-поток дважды.
- *Как:*
  ```python
  from locklib import LockTraceWrapper
  Observer_real, FilteredEventHandler_real = TransactionalCrawler._load_watchdog()
  tc = crawler.transaction
  tc._lifecycle_lock = LockTraceWrapper(tc._lifecycle_lock)
  wrapped_lock = tc._lifecycle_lock
  class TracedObserver(Observer_real):
      def start(self):
          wrapped_lock.notify('observer_start_call')
          super().start()
  monkeypatch.setattr(TransactionalCrawler, '_load_watchdog',
                      staticmethod(lambda: (TracedObserver, FilteredEventHandler_real)))
  tc._start()
  wrapped_lock.was_event_locked('observer_start_call', raise_exception=True)
  tc._stop()
  ```

**14. `test_stop_state_transition_happens_under_lifecycle_lock`**
- *Property:* В `TC._stop()` вызов `_observer.stop()` происходит **под `_lifecycle_lock`**.
- *Как:* аналогично `test_start_state_transition_happens_under_lifecycle_lock`, но обёртка переопределяет `stop()` вместо `start()`:
  ```python
  class TracedObserver(Observer_real):
      def stop(self):
          wrapped_lock.notify('observer_stop_call')
          super().stop()
  ```
  После `tc._stop()` — `wrapped_lock.was_event_locked('observer_stop_call', raise_exception=True)`.

**15. `test_observer_join_happens_OUTSIDE_lifecycle_lock`**
- *Property:* `observer.join()` вызывается **вне** `_lifecycle_lock` — иначе зависший observer-поток (бывает на macOS FSEvents) держал бы lock навечно и любая попытка acquire привела бы к deadlock'у.
- *Как:* аналогично `test_start_state_transition_happens_under_lifecycle_lock`, обёртка переопределяет `join()`:
  ```python
  class TracedObserver(Observer_real):
      def join(self, timeout=None):
          wrapped_lock.notify('observer_join_call')
          super().join(timeout)
  ```
  После `tc._stop()` — assert, что join произошёл **вне** lock'а:
  ```python
  assert not wrapped_lock.was_event_locked('observer_join_call', raise_exception=False)
  ```
  Если кто-то рефакторит `_stop()` и помещает `join()` под lock — этот тест поймает регрессию.

**16. `test_observer_cached_property_first_access_under_lifecycle_lock`**
- *Property:* Первый доступ к `_observer` (триггерящий cached_property body: `_load_watchdog` + создание Observer + handler-schedule) происходит **под `_lifecycle_lock`**. Это критично: `cached_property` не thread-safe до Python 3.13 — без lock'а параллельный первый доступ из двух потоков мог бы создать два Observer'а и потерять один.
- *Как:* spy на `TransactionalCrawler._load_watchdog` через wrapper с `notify('observer_first_init')` перед делегированием. `tc._lifecycle_lock = LockTraceWrapper(tc._lifecycle_lock)`. После `tc._start()` — `tc._lifecycle_lock.was_event_locked('observer_first_init', raise_exception=True)`.

**17. `test_change_alarm_cached_property_first_access_under_lifecycle_lock`**
- *Property:* Первый доступ к `_change_alarm` (создаёт `ChangeAlarm()`) тоже под `_lifecycle_lock`. По той же причине thread-safety cached_property. Происходит транзитивно через body `_observer.cached_property`, который зовётся из `_start()` под lock'ом.
- *Как:* spy на `ChangeAlarm.__init__` через monkeypatch с `notify('change_alarm_first_init')`. После `tc._start()` — `tc._lifecycle_lock.was_event_locked('change_alarm_first_init', raise_exception=True)`.

**18. `test_change_alarm_set_first_writes_data_under_lock`**
- *Property:* В `ChangeAlarm.set_first()` запись `self.__message` (name-mangled до `_ChangeAlarm__message`) и `self._event.set()` происходят под `_lock` (контракт first-wins).
- *Как:* обернуть существующий lock через `state._lock = LockTraceWrapper(state._lock)`. Подменить `_do_record` **на инстансе** (`state._do_record = traced`); обёртка делает `state._lock.notify('write')` перед делегированием. После `state.set_first(...)` — `state._lock.was_event_locked('write', raise_exception=True)`.

### Filtering

**19. `test_filtered_event_does_not_trip_iteration`**
- *Property:* Событие, отфильтрованное любым из трёх механизмов (`exclude`, `extensions`, custom `filter`), не срывает обход. Все три механизма проверяются inner for-loop в одном тесте.
- *Как:* sentinel-pattern (см. TDD-стратегия выше) с inner for-loop по тройкам `(crawler_factory, ignored_file_name, allowed_sentinel_name)`. **Setup**: для путей с subdir'ами (`build/x.txt`) тест предварительно делает `(tmp_path / parent).mkdir(parents=True, exist_ok=True)` перед `touch()` — иначе `Path.touch()` упадёт `FileNotFoundError` на отсутствующем родителе:
  ```python
  scenarios = [
      (lambda root: Crawler(root, exclude=['build/**']),  'build/x.txt',  'sentinel.txt'),
      (lambda root: Crawler(root, extensions=['.py']),     'noise.txt',   'sentinel.py'),
      (lambda root: Crawler(root, filter=lambda p: 'ignore' not in p.name),  'ignore_me.txt',  'sentinel.txt'),
  ]
  for build_crawler, ignored_name, sentinel_name in scenarios:
      ignored_path = tmp_path / ignored_name
      ignored_path.parent.mkdir(parents=True, exist_ok=True)   # ensure parent dirs exist
      sentinel_path = tmp_path / sentinel_name
      sentinel_path.parent.mkdir(parents=True, exist_ok=True)
      # ... остальная sentinel-pattern logic
  ```

**20. `test_filter_exception_in_event_thread_is_treated_as_trip`**
- *Property:* Если пользовательский `filter` падает с исключением в фоновом потоке handler-а — это считается trip-ом (безопасный дефолт, лучше прервать, чем проглотить).
- *Как:* `Crawler(tmp_path, filter=lambda path: (_ for _ in ()).throw(ValueError('oops')))`. Главный поток в transaction. Worker создаёт файл. Ожидаем `TransactionalChangeError`. Проверяем, что итерация прервана (а не зависла и не вернула пустоту молча).

**21. `test_exclude_pattern_matches_deeply_nested_paths_in_handler`**
- *Property:* `exclude=['build/**']` рекурсивно фильтрует все вложенные пути (поведение `pathspec.gitwildmatch`). Проверяем, что handler применяет exclude на любой глубине.
- *Как:* sentinel-pattern. `Crawler(tmp_path, exclude=['build/**'])`. Setup: `tmp_path/'build'/'level1'/'level2'/` (mkdir). Worker трогает `build/level1/level2/deep.txt` (excluded), затем `sentinel.txt` (allowed). Проверка: `get_message()` содержит `sentinel.txt`, не `deep.txt`.

**22. `test_multiple_exclude_patterns_all_apply_in_handler`**
- *Property:* Несколько exclude-pattern'ов одновременно (`exclude=['build/**', '*.tmp', 'docs/']`) — handler применяет **все**. Событие, попадающее в ЛЮБОЙ из них, игнорируется.
- *Как:* sentinel-pattern. Setup: `(tmp_path/'build').mkdir(); (tmp_path/'docs').mkdir()`. Worker через barrier по очереди: `build/x.txt`, `foo.tmp`, `docs/a.txt` (все excluded по разным pattern'ам), затем `sentinel.txt`. Проверка: `get_message()` содержит `sentinel.txt`, не `build`/`.tmp`/`docs`.

**23. `test_multiple_extensions_all_recognized_in_handler`**
- *Property:* `extensions=['.py', '.txt']` — handler trip'ит на файле с **любым** из перечисленных расширений.
- *Как:* sentinel-pattern с двумя подсценариями: (1) `noise.md` (ignored) → `sentinel.py` (trip); (2) `other.css` (ignored) → `sentinel.txt` (trip). Подтверждает, что handler матчит **множественные** допустимые расширения, не только первое.

**24. `test_directory_event_is_ignored_when_only_files_true`**
- *Property:* При `only_files=True` (default) события про директории сами по себе не триггерят. Только file-event внутри триггерит.
- *Как:* sentinel-pattern. Worker через barrier делает `(tmp_path/'new_dir').mkdir()` (dir-event, должен быть игнорирован), затем сразу `(tmp_path/'sentinel.txt').touch()` (file-event, должен trip-нуть). Главный поток ждёт `scope._change_alarm.wait(timeout=2.0)`, проверяет `scope._change_alarm.get_message()` содержит `sentinel.txt`, **не** содержит `new_dir`. Детерминированно: если бы dir-event сработал — first-wins зафиксировал бы его раньше sentinel.

### Multiple iterations / Observer lifecycle

**25. `test_observer_lives_whole_transaction_catches_change_between_iterations`**
- *Property:* Внутри одного `with` Observer живёт всё время → изменение между двумя последовательными итерациями ловится во второй.
- *Как:* `with crawler.transaction as scope: list(scope); (tmp_path/'new').touch(); scope._change_alarm.wait(timeout=2.0); next(iter(scope))` внутри `pytest.raises(TransactionalChangeError)`. Подтверждает, что Observer один на сессию, не per-go(). Использует `_change_alarm.wait()` вместо `time.sleep`.

**26. `test_two_sequential_with_blocks_on_same_crawler_are_isolated`**
- *Property:* После выхода из первого `with` (даже сорванного) состояние не наследуется во второй.
- *Как:* первый with провоцирует ChangeError через worker и barrier. После — `assert scope_first._observer.is_alive() is False and scope_first._active is False`. Чистим дерево обратно. Второй with на чистом дереве проходит до конца. Проверяем, что второй `scope_second is not scope_first` и `scope_second._change_alarm is not scope_first._change_alarm` (разные TC, разные ChangeAlarm'ы).

**27. `test_observer_stopped_when_generator_is_garbage_collected_mid_iteration`**
- *Property:* Если генератор `scope.go()` создан, частично исчерпан и брошен — Observer всё равно завершается через `__exit__` контекстника.
- *Как:* `with crawler.transaction as scope: gen = scope.go(); next(gen); del gen; gc.collect()`. После with — `scope._observer.is_alive() is False`. Это тестирует, что мы не зависим от `GeneratorExit` для cleanup'а Observer'а (так и есть — Observer в start/stop, а не в try/finally внутри go).

**28. `test_observer_thread_is_dead_after_with_exit`**
- *Property:* После корректного завершения `with` watchdog Observer-поток реально остановлен (`.is_alive() is False`). Это гарантирует, что нет фоновых потоков, продолжающих работать после транзакции.
- *Как:* `with crawler.transaction as scope: list(scope)` — обход до конца. После with: `assert scope._observer.is_alive() is False`. Это единственный assert — touch не нужен (`touch()` сам по себе исключения не бросает в любом контексте; проверять «никаких исключений» бессмысленно). Если хочется явно проверить «scope не реагирует на новые события» — это покрывается тестом `test_reuse_scope_after_with_exit_raises_inactive_error`: scope в неактивном состоянии не итерируется.

### Thread safety

**29. `test_two_threads_each_with_own_transaction_are_fully_isolated`**
- *Property:* Два параллельных `with`-блока в разных потоках не аффектят друг друга: срабатывание в одном не отменяет другой.
- *Как:* два разных `Crawler` на одной `tmp_path` с **разными** `exclude` (`exclude=['part_a/**']` и `exclude=['part_b/**']`). Каждый поток держит свой scope в **локальной** переменной (не в общем списке), чтобы не путать индексы:
  ```python
  threading.Barrier(3, ...)                  # все 3 потока (main + A + B) на barrier
  errors = {}                                # thread_name -> caught TransactionalChangeError
  def worker(name, crawler):
      try:
          with crawler.transaction as scope:
              barrier.wait()                  # rendezvous: оба observer подняты
              # триггерим мутации синхронно
              if name == 'A':
                  (tmp_path/'part_a'/'x').touch()  # excluded у A, видим у B
              else:
                  (tmp_path/'part_b'/'x').touch()  # excluded у B, видим у A
              # ждём, что ЧУЖОЕ событие реально дошло до НАШЕГО observer'а
              # (на нашем scope — без индекса в общий список, чтобы исключить путаницу)
              assert scope._change_alarm.wait(timeout=2.0)
              list(scope)                      # iter триггерит _check_token → TransactionalChangeError
      except TransactionalChangeError as exc:
          errors[name] = exc
  threading.Thread(target=worker, args=('A', c_a)).start()
  threading.Thread(target=worker, args=('B', c_b)).start()
  barrier.wait()                             # main pулит синхронизацию
  # ждём, оба потока завершились
  for t in threads: t.join(timeout=5.0)
  assert 'A' in errors and 'B' in errors
  assert 'part_b/x' in str(errors['A'])      # A видел B-шную мутацию
  assert 'part_a/x' in str(errors['B'])      # B видел A-шную мутацию
  ```
  Ключевое отличие от старой формулировки: каждый поток ждёт `wait()` на **своём** `scope._change_alarm`, никаких общих списков и индексов. У `TransactionalChangeError` структурированных полей нет — проверка через содержимое message.

**30. `test_two_concurrent_transactions_have_distinct_observers_and_events`**
- *Property:* Внутренние ресурсы (Observer, Event, ChangeAlarm) у двух одновременно активных TC — разные объекты.
- *Как:* запустить два потока, каждый делает `tc = crawler.transaction; tc._start()`, оба синхронизируются через `Barrier(3)` так, чтобы быть активными одновременно. Главный поток: `assert tc1._observer is not tc2._observer` и `assert tc1._change_alarm is not tc2._change_alarm` (поскольку Event живёт внутри ChangeAlarm — этого достаточно). Потом `tc1._stop(); tc2._stop()`.

### apply integration

**31. `test_apply_transactional_true_smoke_without_changes`**
- *Property:* `crawler.apply(fn, transactional=True)` без изменений отрабатывает корректно и вызывает callback для каждого пути.
- *Как:* заполнить дерево, `collected = []; crawler.apply(collected.append, transactional=True)`. `assert sorted(collected) == sorted(expected)`.

**32. `test_apply_transactional_true_raises_on_change_and_stops_calling_function`**
- *Property:* При изменении **между путями** во время `apply(transactional=True)` (после callback #1, до callback #2): поднимается `TransactionalChangeError`, callback вызывается **ровно один раз** — `_check_token` срабатывает на condition_token до следующего yield, последующие callback'и не запускаются. Покрывает раннюю мутацию. **Не путать с `test_mutation_during_last_element_*` (#55)**, где мутация происходит **после последнего** callback'а — там все N callback'ов успевают отработать до раиза.
- *Docstring*: для детерминированного `len(collected) == 1` нужны **два barrier** (один синхронизирует callback#1 ↔ worker; второй — момент «event реально доставлен handler'у»). Однобарьерный подход racy: между worker'овским `touch()` и видимостью alarm-cancellation в condition_token есть OS→dispatcher→handler-окно, в которое callback#2 может проскочить. Двойной barrier закрывает это окно: worker делает touch → ждёт `_change_alarm.wait()` (alarm set) → только потом отпускает callback. Следующий `next()` гарантированно видит cancellation.
- *Как:*
  ```python
  barrier_at_first = threading.Barrier(2)
  barrier_event_delivered = threading.Barrier(2)
  collected = []
  captured_tc = []
  # Перехватываем `crawler.transaction` чтобы worker мог обратиться к TC, который apply()
  # создаёт внутри своего `with self.transaction as scope:`. Без этого scope невидим снаружи.
  original_transaction = type(crawler).transaction.fget
  monkeypatch.setattr(
      type(crawler), 'transaction',
      property(lambda self: captured_tc.append(tc := original_transaction(self)) or tc),
  )
  def callback(path):
      collected.append(path)
      if len(collected) == 1:
          barrier_at_first.wait()
          barrier_event_delivered.wait()
  def worker():
      barrier_at_first.wait()
      (tmp_path/'new.txt').touch()
      assert captured_tc[0]._change_alarm.wait(timeout=2.0)   # alarm set
      barrier_event_delivered.wait()                          # signal callback to return
  threading.Thread(target=worker).start()
  with pytest.raises(TransactionalChangeError):
      crawler.apply(callback, transactional=True)   # ← тестируем apply() напрямую
  assert len(collected) == 1
  ```

**33. `test_apply_transactional_true_stops_observer_when_callback_raises`**
- *Property:* Если callback в `apply(transactional=True)` кидает исключение, Observer всё равно корректно остановлен (через `__exit__` внутреннего `with`).
- *Как:* monkeypatch'нуть `TransactionalCrawler._load_watchdog`, подменив Observer-класс на наследника, который при `__init__` добавляет свой инстанс в shared `observer_instances` list:
  ```python
  Observer_real, FilteredEventHandler_real = TransactionalCrawler._load_watchdog()
  observer_instances = []
  class TracedObserver(Observer_real):
      def __init__(self):
          observer_instances.append(self)
          super().__init__()
  monkeypatch.setattr(TransactionalCrawler, '_load_watchdog',
                      staticmethod(lambda: (TracedObserver, FilteredEventHandler_real)))
  def callback(p):
      raise RuntimeError('boom')
  with pytest.raises(RuntimeError, match=match('boom')):
      crawler.apply(callback, transactional=True)
  assert observer_instances[-1].is_alive() is False    # observer thread terminated despite the exception
  ```

**34. `test_non_transactional_operations_never_load_watchdog`**
- *Property:* Операции, которые НЕ должны затрагивать transactional-логику, не импортируют и не дёргают watchdog. Гарантия, что обычные пользователи не платят за optional-зависимость.
- *Как:* inner for-loop по триггерам:
  ```python
  monkeypatch.setattr(TransactionalCrawler, '_load_watchdog',
                      staticmethod(lambda: (_ for _ in ()).throw(RuntimeError('must not be called'))))
  triggers = [
      lambda c: c.apply(lambda p: None),       # apply(transactional=False default)
      lambda c: c.transaction,                  # access property (lazy)
      lambda c: repr(c.transaction),            # repr of fresh TC (does not trigger _start)
  ]
  for trigger in triggers:
      trigger(crawler)                          # must not raise
  ```

### Errors / dependency

**35. `test_missing_watchdog_raises_clear_import_error_with_install_hint`**
- *Property:* При отсутствии `watchdog` реальный код `_load_watchdog` (которая делает `from watchdog.observers import Observer`) ловит `ImportError` и поднимает `WatchdogNotInstalledError` с точной командой установки в сообщении. Проверяется именно реальный `try/except ImportError → raise WatchdogNotInstalledError` путь, а не его propagation.
- *Как:* Симулируем отсутствие пакета через подмену `sys.modules['watchdog']` на объект, который при импорте подмодулей бросает `ImportError`:
  ```python
  import sys
  # Подменяем верхний модуль так, чтобы `from watchdog.observers import Observer` упал ImportError.
  # Реальный _load_watchdog поймает его в try/except и переподнимет WatchdogNotInstalledError.
  monkeypatch.setitem(sys.modules, 'watchdog', None)
  monkeypatch.setitem(sys.modules, 'watchdog.observers', None)
  monkeypatch.setitem(sys.modules, 'watchdog.events', None)
  TransactionalCrawler._load_watchdog.cache_clear()        # cache must not return previously-loaded objects
  with pytest.raises(WatchdogNotInstalledError,
                     match=match('watchdog is not installed. Install it with: pip install dirstree[transactional]')):
      crawler.transaction._start()
  ```
  `sys.modules[name] = None` — стандартный pytest pattern для симуляции missing-module: при `import name` Python видит `None` в `sys.modules` и поднимает `ImportError`. Это покрывает **реальный** try/except в `_load_watchdog`, а не propagation монкейпатченного исключения.

**36. `test_watchdog_not_installed_error_is_also_subclass_of_import_error`**
- *Как:* `assert issubclass(WatchdogNotInstalledError, ImportError)`; также `assert issubclass(WatchdogNotInstalledError, DirstreeError)`. Гарантирует, что `except ImportError` тоже ловит наш класс.

**37. `test_transactional_change_error_is_not_subclass_of_cancellation_error`**
- *Как:* `assert not issubclass(TransactionalChangeError, CancellationError)`. Гарантирует, что `except CancellationError` не съест наш сигнал. Поведенческая end-to-end проверка («raise from condition_token → конвертируется в TransactionalChangeError, не съедается `except CancellationError`») уже покрыта тестом `test_internal_condition_token_converts_to_transactional_change_error`.

### Interaction with existing options + token semantics

**38. `test_frozen_plus_transactional_raises_when_change_happens_during_snapshot_build`**
- *Property:* При `freeze=True` и transactional, изменение во время сбора snapshot тоже срабатывает. Проверяется inner for-loop'ом по `Crawler` и `PythonCrawler` (обоим есть `freeze`). Замечание: используем inner for-loop вместо `@pytest.mark.parametrize` — соответствует CLAUDE.md convention; при fail возможна потеря per-iteration isolation, но эта цена осознанно платится.
- *Как:* inner for-loop по двум source-классам:
  ```python
  for source_class in [Crawler, PythonCrawler]:
      crawler = source_class(tmp_path, freeze=True, filter=filter_with_barrier)
      # worker через barrier мутирует
      with pytest.raises(TransactionalChangeError):
          with crawler.transaction as scope: list(scope)
      # snapshot НЕ собрался до конца
      assert yielded_count < expected_count
  ```

**39. `test_raise_on_cancel_false_does_not_silence_transactional_change`**
- *Property:* Даже при `raise_on_cancel=False` на source-краулере, TC всё равно поднимает `TransactionalChangeError`. Проверяется inner for-loop'ом по `Crawler` и `PythonCrawler`.
- *Как:* inner for-loop по двум source-классам:
  ```python
  for source_class in [Crawler, PythonCrawler]:
      crawler = source_class(tmp_path, raise_on_cancel=False)
      # worker через barrier мутирует
      with pytest.raises(TransactionalChangeError):
          with crawler.transaction as scope: list(scope)
  ```

**40. `test_external_cancellation_token_propagates_through_transaction_as_cancellation_error`**
- *Property:* Если cancellation приходит **извне** (от source-токена в конструкторе Crawler ИЛИ от user-токена в `scope.go(token)`), итерация останавливается с `CancellationError`, **не** с `TransactionalChangeError`. Внутренний ConditionToken не «съедает» внешний сигнал.
- *Как:* inner for-loop по двум сценариям:
  ```python
  scenarios = [
      ('source_token', lambda: Crawler(tmp_path, token=SimpleToken(cancelled=True), raise_on_cancel=True),
                       lambda scope: list(scope)),
      ('user_token',   lambda: Crawler(tmp_path, raise_on_cancel=True),
                       lambda scope: list(scope.go(SimpleToken(cancelled=True)))),
  ]
  for source_name, build_crawler, iterate in scenarios:
      with pytest.raises(CancellationError) as exc_info:
          with build_crawler().transaction as scope:
              iterate(scope)
      assert not isinstance(exc_info.value, TransactionalChangeError)
  ```

**41. `test_internal_condition_token_converts_to_transactional_change_error`**
- *Property:* Только cancellation от нашего internal ConditionToken (т.е. реальное изменение в директории) конвертируется в `TransactionalChangeError`. Это разделение обеспечивается `exc.token is condition_token`-проверкой.
- *Как:* Сценарий `test_each_file_event_type_during_iteration_raises_with_correct_message` (`created` event during iteration) с явным проверкой типа исключения через `pytest.raises(TransactionalChangeError)`. Подтверждает, что только наш токен переводит в доменное исключение.

**42. `test_custom_raise_on_cancel_disambiguates_token_sources_via_cause`**
- *Property:* При `source.raise_on_cancel=CustomError` cantok оборачивает CancellationError → CustomError через `raise X from original`. Наш `go()` инспектирует `__cause__.token` и решает, что отдать пользователю: **наш condition_token → `TransactionalChangeError`** (внутренний сигнал); **user/source token → CustomError как есть** (внешний). Покрывает обе ветки disambiguation'а.
- *Как:* inner for-loop по двум зеркальным сценариям. Для `fs_mutation` — реальный сценарий с barrier+filter (yielded_target Event как в `test_each_file_event_type_during_iteration_raises_with_correct_message`, `created` event). Для `source_user_token` — cancel сразу:
  ```python
  scenarios = [
      ('our_condition_token', 'fs_mutation',
       TransactionalChangeError,
       'Directory changed during transactional iteration: created at .*new\\.txt'),
      ('source_user_token',  'no_mutation',
       ValueError,                                              # пробрасывается как есть, НЕ TransactionalChangeError
       None),
  ]
  for desc, trigger, expected_exc, expected_match in scenarios:
      if trigger == 'fs_mutation':
          # построение `created` сценария (как в test_each_file_event_type_*)
          yielded_target = threading.Event()
          def filter_(path):
              if path.name == 'new.txt':                        # target file
                  yielded_target.set()
              return True
          def worker():
              assert yielded_target.wait(timeout=2.0)
              (tmp_path/'new.txt').touch()
              assert scope._change_alarm.wait(timeout=2.0)
          threading.Thread(target=worker).start()
          crawler = Crawler(tmp_path, filter=filter_, raise_on_cancel=ValueError)
      else:                                                     # source_user_token
          crawler = Crawler(tmp_path, token=SimpleToken(cancelled=True), raise_on_cancel=ValueError)
      raises_kwargs = {} if expected_match is None else {'match': match(expected_match)}
      with pytest.raises(expected_exc, **raises_kwargs):
          with crawler.transaction as scope:
              list(scope)
  ```

**43. `test_group_transaction_property_returns_tc_with_group_as_source`**
- *Property:* `(c1 + c2).transaction` возвращает `TransactionalCrawler`, у которого `source` — **та же** `CrawlersGroup`-инстанция (не оборачивается рекурсивно).
- *Как:* `group = Crawler('a') + Crawler('b')`. `outer = group.transaction`. `assert isinstance(outer, TransactionalCrawler)`. `assert outer.source is group`.

**44. `test_crawlers_group_apply_transactional_true_works_end_to_end`**
- *Property:* `(c1 + c2).apply(fn, transactional=True)` корректно работает: union путей из всех children, защищён через transaction, мутация во время выполнения триггерит `TransactionalChangeError`.
- *Как:* два tmp-каталога. (a) Smoke без мутации: `collected = []; (Crawler(a) + Crawler(b)).apply(collected.append, transactional=True)`. Проверить, что `collected` содержит union. (b) С мутацией: callback после первого вызова через barrier инициирует worker, который touch'ит файл в одном из child-paths. Ожидаем `TransactionalChangeError`.

**45. `test_group_transaction_yields_files_from_all_children_when_static`**
- *Property:* `with group.transaction as scope` без мутаций возвращает union путей всех children (с дедупликацией, как у обычной `CrawlersGroup.go`).
- *Как:* два временных каталога, в каждом по несколько файлов. `expected = sorted(set(Crawler(a)) | set(Crawler(b)))`. `with (Crawler(a) + Crawler(b)).transaction as scope: actual = sorted(set(scope))`. `assert actual == expected`.

**46. `test_group_transaction_trips_on_change_in_first_child_path`**
- *Property:* Изменение в наблюдаемой области первого child сбивает обход.
- *Как:* group из двух Crawler. Изменение в первом → `TransactionalChangeError`, путь из первого. Через barrier + filter синхронизация.

**47. `test_group_transaction_trips_when_change_happens_in_currently_iterated_child`**
- *Property:* Изменение в **текущем** child'е (которого сейчас обходим), после исчерпания предыдущих — срывает обход. Стандартный сценарий «мутация в активной области наблюдения».
- *Как:* group из двух Crawler (A, B). Уже прошли через A (исчерпан), сейчас итерируем B. Worker трогает файл **в B** → следующий yield в B поднимает `TransactionalChangeError` с путём из B.

**48. `test_group_transaction_trips_when_change_happens_in_already_exhausted_child`**
- *Property:* Изменение в **уже исчерпанном** child'е (его обход завершён) во время итерации **следующего** — всё равно срывает обход. Ключевой тест транзакционной семантики: shared `ChangeAlarm` обеспечивает global watch на всю группу, не только на текущий active leaf.
- *Как:* group из двух Crawler (A, B). Уже прошли через A (исчерпан), сейчас итерируем B. Worker трогает файл **в A** (НЕ в B!) → следующий yield в B поднимает `TransactionalChangeError` с путём из **A**.

Различие между `test_group_transaction_trips_when_change_happens_in_currently_iterated_child` и `test_group_transaction_trips_when_change_happens_in_already_exhausted_child`: в первом мутация в текущем активном child'е (B, тот что итерируем), во втором — в уже исчерпанном (A). Второй доказывает централизованную схему — без shared ChangeAlarm мутация в A была бы потеряна.

**49. `test_group_transaction_uses_separate_handler_per_child_with_independent_filters_non_overlapping_paths`**
- *Property:* Когда листы в группе указывают на **непересекающиеся** paths, каждый применяет свой набор фильтров изолированно: событие, попавшее в exclude одного листа, не вызывает trip ни через какой другой лист (event просто не доходит до них, потому что они в другой директории). (Случай **перекрывающихся** paths покрывается отдельно тестом `test_overlapping_paths_in_group_use_distinct_handlers_with_independent_filters`.)
- *Как:* `c1 = Crawler(a, exclude=['ignored/**'])`, `c2 = Crawler(b)` (непересекающиеся paths). Сценарии: (1) worker трогает `a/ignored/x.txt` — c1-excluded, c2 не видит → no trip; (2) sanity — worker трогает `a/regular.txt` → trip от c1.

**50. `test_transactional_crawler_transaction_property_idempotent_across_chain_depth`**
- *Property:* `tc.transaction` на активном TC возвращает self (идемпотентно); цепочка `.transaction.transaction…` любой глубины — то же self (рекурсивная идемпотентность).
- *Как:* inner for-loop по глубинам:
  ```python
  with crawler.transaction as scope:
      for depth in [1, 3, 5]:
          obj = scope
          for _ in range(depth):
              obj = obj.transaction
          assert obj is scope
  ```

### TC.apply override + observer reuse

**51. `test_tc_apply_uses_same_observer_instance_as_scope_observer`**
- *Property:* Observer, используемый при `scope.apply(fn)`, идентичен `scope._observer` через `is`-сравнение.
- *Как:* `with crawler.transaction as scope: observer_before = scope._observer; scope.apply(lambda p: None); assert scope._observer is observer_before`.

**52. `test_tc_apply_callback_runs_under_existing_observer_protection`**
- *Property:* Если во время выполнения callback'а в `scope.apply(fn)` происходит мутация, она ловится через тот же `_change_alarm` — реально работает существующий observer, не пустышка.
- *Как:* `with crawler.transaction as scope:` → callback в `apply` после первого вызова через barrier инициирует мутацию в worker-потоке → next yield в `apply` поднимает `TransactionalChangeError`. Подтверждает, что existing observer наблюдает.

**53. `test_tc_apply_with_transactional_false_raises_invalid_argument_error`**
- *Property:* Явная передача `transactional=False` в `TC.apply()` кидает `InvalidTransactionalArgumentError` с понятным сообщением.
- *Как:* `with crawler.transaction as scope: with pytest.raises(InvalidTransactionalArgumentError, match=match('Cannot pass transactional=False to TransactionalCrawler.apply(). TransactionalCrawler is always transactional; this kwarg is kept only for signature compatibility with non-transactional crawlers.')): scope.apply(lambda p: None, transactional=False)`.

**54. `test_tc_apply_transactional_true_explicit_behaves_same_as_default`**
- *Property:* `scope.apply(fn, transactional=True)` поведенчески идентичен `scope.apply(fn)` (default — True на TC).
- *Как:* собрать пути дважды: `collected_default = []; scope.apply(collected_default.append)` и `collected_explicit = []; scope.apply(collected_explicit.append, transactional=True)`. `assert sorted(collected_default) == sorted(collected_explicit)`.

### Last-iteration / last-callback protection

**55. `test_mutation_during_last_element_is_caught_through_both_iteration_and_apply`**
- *Property:* Мутация, происходящая на **последнем** элементе обхода (между последним yield/callback и финальной проверкой `_change_alarm.is_alarmed()` в `__exit__`), всё равно ловится — observer жив до самого выхода из `with`. Покрывает оба пути: прямую итерацию (`scope.go()`) и `scope.apply()`. Все N callback'ов в `apply`-режиме **успевают отработать** до final-check'а, поэтому `len(collected) == total`. **Не путать с `test_apply_transactional_true_raises_on_change_and_stops_calling_function` (#32)**, где мутация происходит **между** callback'ами и обработка прерывается на следующем yield.
- *Как:* counter-based detection последнего элемента (не зависит от rglob-порядка). `total` через **`rglob` с теми же фильтрами**, что `Crawler._traverse` (`iterdir()` нерекурсивный — даст неверный count при subdir'ах). Inner for-loop по двум режимам; counter инкрементируется в filter (iter-mode) или callback (apply-mode), оба варианта дают `counter == total` к концу:
  ```python
  # рекурсивный count файлов (соответствует обходу Crawler в only_files=True)
  total = sum(1 for p in tmp_path.rglob('*') if p.is_file())
  for mode in ['iter', 'apply']:
      counter = [0]
      barrier = threading.Barrier(2)
      def on_last_element_trigger_worker():
          counter[0] += 1
          if counter[0] == total:
              barrier.wait()                                  # синхронизация с worker
      def worker():
          barrier.wait()
          (tmp_path/'new.txt').touch()
          assert scope._change_alarm.wait(timeout=2.0)
      threading.Thread(target=worker).start()
      collected = []
      def callback(p):
          collected.append(p)
          on_last_element_trigger_worker()
      crawler = Crawler(tmp_path, filter=lambda p: (on_last_element_trigger_worker(), True)[1] if mode == 'iter' else True)
      with pytest.raises(TransactionalChangeError):
          with crawler.transaction as scope:
              if mode == 'iter':
                  list(scope)
              else:
                  scope.apply(callback)
      # assert ВНЕ pytest.raises (после exception propagation) — иначе недостижим
      if mode == 'apply':
          assert len(collected) == total                       # последний callback успел выполниться до raise
  ```

### Stop during iteration

**56. `test_stop_during_iteration_aborts_workers_with_clear_error`**
- *Property:* Если главный поток выходит из `with crawler.transaction:` пока worker-потоки ещё итерируют `scope`, workers получают `TransactionStoppedDuringIterationError` на следующем `next()` вместо silent corruption. Реализуется через condition_token, который ловит не только trip, но и `_active is False`.
- *Как:* worker-исключения **нельзя** ловить через `pytest.raises` из главного потока — нужен shared-container `worker_exc = []`. И **нельзя** использовать `threading.Barrier(2)` для всех rendezvous (после первого release barrier требует следующих 2-х участников; main уже ушёл, worker зависнет навечно). Используем **`threading.Event`** для one-shot main→worker сигналинга.
  ```python
  worker_started = threading.Barrier(2)             # rendezvous: worker hit first yield
  main_exited = threading.Event()                   # main signals worker to resume after _stop()
  worker_exc = []                                   # captures exception type
  with crawler.transaction as scope:
      def work():
          try:
              for path in scope:
                  worker_started.wait(timeout=2.0)  # rendezvous on first yield
                  main_exited.wait(timeout=2.0)     # block until main has called _stop()
          except BaseException as exc:
              worker_exc.append(exc)
      worker = threading.Thread(target=work)
      worker.start()
      worker_started.wait(timeout=2.0)
  main_exited.set()                                 # outside `with` → _stop() done → release worker
  worker.join(timeout=5.0)
  assert len(worker_exc) == 1
  assert isinstance(worker_exc[0], TransactionStoppedDuringIterationError)
  assert match('Transaction was stopped while iteration was in progress.').search(str(worker_exc[0]))
  ```

**57. `test_concurrent_iteration_completes_normally_when_main_thread_waits_for_workers`**
- *Property:* Happy path: если главный поток корректно ждёт worker'ов перед выходом из `with` — никаких ошибок, обход проходит чисто.
- *Как:* `with crawler.transaction as scope:` → запустить 4 worker'а через `ThreadPoolExecutor`, каждый собирает paths в общий list. `executor.shutdown(wait=True)` до выхода из `with`. Проверить, что список содержит ожидаемые paths (union из всех workers), никаких исключений. Регрессионный тест: документирует **правильный** паттерн multi-threaded использования.

### Nested with

**58. `test_nested_with_crawler_transaction_in_same_thread_works`**
- *Property:* Вложенные `with c.transaction as outer:` + внутри `with c.transaction as inner:` в одном потоке корректно работают: `inner` и `outer` — разные TC, у каждого свой Observer и ChangeAlarm. (Они разные именно потому, что `c.transaction` (на исходном Crawler) вызвано дважды и каждый вызов возвращает новый TC — это не сработало бы для `scope.transaction`, который на active TC возвращает self.) Мутация во время `inner` срывает `inner`, не аффектит `outer`.
- *Как:*
  ```python
  with c.transaction as outer:
      with c.transaction as inner:
          assert inner is not outer
          assert inner._observer is not outer._observer
          assert inner._change_alarm is not outer._change_alarm
          # ... inner работает корректно
      # outer всё ещё активен после выхода из inner
      assert outer._active is True
  ```

### Apply + token composition

**59. `test_apply_transactional_true_with_user_token_can_be_cancelled_externally`**
- *Property:* `crawler.apply(fn, token=user_token, transactional=True)` — user_token композируется с внутренним ConditionToken и может отменить итерацию извне.
- *Как:* `user_token = SimpleToken()`; в worker-потоке после первого callback (через barrier): `user_token.cancel()`. Главный: `with pytest.raises(CancellationError): crawler.apply(callback, token=user_token, transactional=True)`. Проверка: это `CancellationError`, не `TransactionalChangeError` (наш condition_token не «съел» user-сигнал).

**60. `test_apply_without_transactional_with_user_token_works_unchanged`**
- *Property:* `crawler.apply(fn, token=user_token)` (default `transactional=False`) ведёт себя как раньше — никаких изменений из-за нашего расширения сигнатуры.
- *Как:* `user_token = SimpleToken(); user_token.cancel()`. `crawler.apply(lambda p: None, token=user_token)` — либо завершается тихо, либо кидает `CancellationError` в зависимости от `crawler.raise_on_cancel`. Поведение совпадает с тем, что было до transactional-фичи. Регрессия-проверка.

### Идемпотентность _load_watchdog

**61. `test_load_watchdog_returns_same_handler_class_on_repeated_calls`**
- *Property:* `_load_watchdog` кешируется через `@functools.lru_cache(maxsize=None)` — повторные вызовы возвращают идентичные объекты (тот же `FilteredEventHandler`-класс, тот же `Observer`-класс). Это побочный эффект кеша и должен быть наблюдаем через `is`-сравнение.
- *Как:* 
  ```python
  obs_cls_1, handler_cls_1 = TransactionalCrawler._load_watchdog()
  obs_cls_2, handler_cls_2 = TransactionalCrawler._load_watchdog()
  assert obs_cls_1 is obs_cls_2
  assert handler_cls_1 is handler_cls_2
  ```
  Без кеша второй вызов создал бы новый `FilteredEventHandler`-класс (т.к. он определён в closure тела метода), и `handler_cls_1 is handler_cls_2` было бы False.

**62. `test_transactional_crawler_transaction_property_raises_when_inactive`**
- *Property:* `tc.transaction` на неактивном TC поднимает `TransactionInactiveError` с понятным сообщением.
- *Как:* `tc = crawler.transaction`. `with pytest.raises(TransactionInactiveError, match=match('Cannot start a new transaction from an inactive TransactionalCrawler. TransactionalCrawler objects are single-use; create a new one via original_crawler.transaction.')): tc.transaction`.

**63. `test_group_transaction_uses_single_observer_with_per_leaf_handlers_sharing_one_change_alarm`**
- *Property:* (1) Outer TC создаёт **ровно один** Observer-инстанс вне зависимости от количества leaf'ов. (2) На каждый leaf — отдельный handler. (3) Все handlers ссылаются на **один и тот же** `ChangeAlarm`. (4) Для вложенных групп (`(c1 + c2) + c3`) расчёт корректный — три leaf'а → три handler'а, три вызова `observer.schedule()` с правильными путями. Совокупность — ядро централизованной схемы.
- *Как:* `FilteredEventHandler` определён в closure `_load_watchdog`, поэтому monkeypatch уровня класса невозможен. Подменяем `_load_watchdog` на версию, возвращающую обёрнутые классы, которые логируют себя:
  ```python
  Observer_real, FilteredEventHandler_real = TransactionalCrawler._load_watchdog()
  created_handlers, observer_instances, scheduled = [], [], []
  class TracedHandler(FilteredEventHandler_real):
      def __init__(self, source, change_alarm):
          created_handlers.append(self)
          super().__init__(source, change_alarm)
  class TracedObserver(Observer_real):
      def __init__(self):
          observer_instances.append(self)
          super().__init__()
      def schedule(self, handler, path, recursive=True):
          scheduled.append((path, handler))
          return super().schedule(handler, path, recursive=recursive)
  monkeypatch.setattr(TransactionalCrawler, '_load_watchdog',
                      staticmethod(lambda: (TracedObserver, TracedHandler)))
  nested = (Crawler('a') + Crawler('b')) + Crawler('c')      # nested group, 3 leaves
  with nested.transaction as scope:
      assert len(observer_instances) == 1                     # ровно один Observer
      assert len(created_handlers) == 3                       # один handler на лист
      for h in created_handlers:
          assert h._change_alarm is scope._change_alarm       # все handlers — один alarm
      assert len(scheduled) == 3                              # три schedule-вызова
      assert set(p for p, _ in scheduled) == {'a', 'b', 'c'}  # правильные пути
  ```
  *Почему не через `tc._observer.emitters`*: атрибут `emitters` — implementation detail watchdog, может измениться между версиями. Спай через `schedule()` (публичный метод) надёжнее.

**64. `test_three_level_nested_groups_unwrap_to_all_leaves`**
- *Property:* Произвольно глубокая вложенность `((c1 + c2) + (c3 + c4)) + c5` корректно разворачивается до пяти leaf-crawler'ов.
- *Как:* построить дерево из 5 листьев в указанной топологии. `tc = group.transaction; tc._start()`. Подсчёт через monkeypatch на `observer.schedule` — пять вызовов. Дополнительно: `with tc: list(tc)` — должен вернуть union содержимого всех пяти директорий.

**65. `test_active_tc_inside_group_is_unwrapped_to_its_source`**
- *Property:* Если в группу вложен **уже активный** TC (`Crawler('a').transaction` внутри `with`), и эту группу обернуть в `.transaction` — внутренний `unwrap_to_crawlers()` развернёт TC до его underlying Crawler. Outer TC создаст handler для underlying Crawler.
- *Как:* `tc_a = Crawler(a).transaction; with tc_a: mixed = tc_a + Crawler(b); with mixed.transaction as scope: list(scope)`. Через monkeypatch на `observer.schedule` проверить, что schedule вызван с путями Crawler(a) и Crawler(b), а не с tc_a как опаком. Документировано как corner-case с дублированием наблюдения.

**66. `test_overlapping_paths_in_group_use_distinct_handlers_with_independent_filters`**
- *Property:* Если два leaf-crawler'а указывают на одну и ту же базовую директорию с разными фильтрами — для каждого создаётся свой handler, применяющий свои фильтры независимо. Watchdog'овская OS-подписка может быть одна (он дедуплицирует), но handler'ов два.
- *Как:* `c1 = Crawler(tmp_path, exclude=['x/**'])`, `c2 = Crawler(tmp_path, exclude=['y/**'])`. `with (c1 + c2).transaction as scope:` — worker трогает `tmp_path/x/file`. Для c1 это excluded → не trip. Для c2 — не excluded → trip. Ожидаем `TransactionalChangeError`. Затем зеркальный сценарий: worker трогает `tmp_path/y/file` → c1 trip, c2 не trip → `TransactionalChangeError`.

### Repr / совместимость с подклассами

**67. `test_repr_of_transactional_crawler_is_stable_across_lifecycle_states`**
- *Property:* `repr(tc)` стабильна, содержит репр source, и отображает `active` kwarg только когда состояние НЕ дефолтное (`None`). Покрывает три lifecycle-состояния: fresh (репр без `active=`), active (`active=True`), stopped (`active=False`).
- *Как:* inner for-loop по трём состояниям:
  ```python
  src = Crawler('some/path')
  fresh = src.transaction
  assert repr(fresh) == "TransactionalCrawler(Crawler('some/path'))"
  with src.transaction as scope_active:
      assert repr(scope_active) == "TransactionalCrawler(Crawler('some/path'), active=True)"
  stopped = src.transaction
  stopped._start(); stopped._stop()
  assert repr(stopped) == "TransactionalCrawler(Crawler('some/path'), active=False)"
  ```
  Покрывает: `not_none`-фильтр скрывает `active=None`; `_active`-state корректно отражается через все три lifecycle-перехода. Полный round-trip через `eval(repr(tc))` намеренно сломан — конструктор кидает `RuntimeError` если `active is not None` (см. docstring `__init__`), потому что TC не входит в публичный API.

**68. `test_external_construction_with_active_kwarg_raises_runtime_error`**
- *Property:* Прямой вызов `TransactionalCrawler(source, active=True)` или `TransactionalCrawler(source, active=False)` кидает `RuntimeError` с понятным сообщением. `TransactionalCrawler(source)` (default `active=None`) работает.
- *Как:* inner for-loop по `True`, `False`:
  ```python
  for value in [True, False]:
      with pytest.raises(RuntimeError, match=match('The `active` argument is reserved for `__repr__` only. TransactionalCrawler instances are created automatically via `source.transaction`. Do not construct one directly.')):
          TransactionalCrawler(crawler, active=value)
  # Sanity: default works:
  tc = TransactionalCrawler(crawler)
  assert tc._active is None
  ```

**69. `test_unsupported_abstract_crawler_subclass_raises_type_error`**
- *Property:* Если в `TransactionalCrawler` передан `AbstractCrawler`-подкласс, не являющийся `Crawler`/`PythonCrawler`/`CrawlersGroup`/`TransactionalCrawler`, попытка обхода через `with`-блок поднимает `TypeError` (срабатывает в `unwrap_to_crawlers` cached_property `_observer`).
- *Как:* определить минимальный custom subclass прямо внутри теста:
  ```python
  class FakeCrawler(AbstractCrawler):
      paths = ['/tmp']
      def go(self, token=DefaultToken()):
          yield from ()
  tc = FakeCrawler().transaction
  expected = match("Unsupported source type 'FakeCrawler' for TransactionalCrawler: expected Crawler, CrawlersGroup, or TransactionalCrawler subclass.")
  with pytest.raises(TypeError, match=expected):
      with tc:
          list(tc)
  ```

**70. `test_python_crawler_transaction_equivalence_across_iteration_apply_and_trip_scenarios`**
- *Property:* `PythonCrawler.transaction` ведёт себя идентично `Crawler.transaction` по трём аспектам: (a) статическая итерация выдаёт тот же набор путей, что обычный обход без transactional; (b) `apply(transactional=True)` без мутаций даёт тот же набор путей, что `apply()`; (c) мутация в директории во время transactional-обхода кидает `TransactionalChangeError`.
- *Как:* `tmp_path` с микс `.py` и `.txt` файлов. Все три проверки в одном тесте через три блока:
  1. `expected = sorted(PythonCrawler(tmp_path).go()); with PythonCrawler(tmp_path).transaction as scope: assert sorted(scope) == expected` (только `.py`).
  2. `collected_plain = []; PythonCrawler(tmp_path).apply(collected_plain.append)`. `collected_tx = []; PythonCrawler(tmp_path).apply(collected_tx.append, transactional=True)`. `assert sorted(collected_plain) == sorted(collected_tx)`.
  3. Воспроизвести `test_each_file_event_type_during_iteration_raises_with_correct_message` (`created` event during iteration) с source = `PythonCrawler(tmp_path)`, worker создаёт `.py` файл, ожидаем `TransactionalChangeError`.

### Subclass / hierarchy invariants

**71. `test_all_new_transactional_errors_are_dirstree_error_subclasses`**
- *Property:* Все шесть новых классов исключений — наследники `DirstreeError`. Обеспечивает единообразный `except DirstreeError` для пользователей. Также проверяет multiple inheritance: `WatchdogNotInstalledError` ловится через `except ImportError`.
- *Как:* inner for-loop по списку классов:
  ```python
  for error_class in [
      TransactionalChangeError,
      TransactionInactiveError,
      TransactionAlreadyActiveError,
      TransactionStoppedDuringIterationError,
      InvalidTransactionalArgumentError,
      WatchdogNotInstalledError,
  ]:
      assert issubclass(error_class, DirstreeError)
      # construction без аргументов работает на всех Py 3.8–3.14t (MRO с минимальным __init__)
      instance = error_class()
      assert isinstance(instance, DirstreeError)
  # дополнительно: WatchdogNotInstalledError также ImportError-наследник
  assert issubclass(WatchdogNotInstalledError, ImportError)
  ```

**Тесты покрытия source-types** (`test_filter_applies_uniformly_across_all_source_types`, `test_lifecycle_state_machine_works_across_all_source_types`, `test_transactional_change_error_message_format_uniform_across_source_types`) описаны ниже — три новых теста, расширяющих покрытие базовых механик на все три source-типа (`Crawler`, `PythonCrawler`, `CrawlersGroup`).

**72. `test_filter_applies_uniformly_across_all_source_types`**
- *Property:* `filter`-callable работает одинаково для всех source-типов: событие, отфильтрованное пользовательской функцией, не trip'ит ChangeAlarm независимо от того, какой именно crawler стоит в source'е.
- *Как:* inner for-loop по 3 source-сборкам. Для каждого crawler с `filter=lambda p: 'ignored' not in p.name`, sentinel-pattern: worker через barrier трогает `ignored_X.txt` (excluded), потом `sentinel.txt` (allowed). `scope._change_alarm.wait()` → проверить, что `get_message()` содержит `sentinel`, не `ignored`.

**73. `test_lifecycle_state_machine_works_across_all_source_types`**
- *Property:* `_active: None → True → False` корректно работает для всех source-типов. Проверяет, что lifecycle-инвариант не привязан к специфике `Crawler`.
- *Как:* inner for-loop по 3 source-сборкам:
  ```python
  for build in source_builders:
      tc = build().transaction
      assert tc._active is None
      tc._start();  assert tc._active is True
      tc._stop();   assert tc._active is False
      with pytest.raises(TransactionInactiveError):
          tc._start()                                # терминальное состояние
  ```

**74. `test_transactional_change_error_message_format_uniform_across_source_types`**
- *Property:* Формат сообщения `TransactionalChangeError` (`Directory changed during transactional iteration: <event> at <path>`) одинаков для всех source-типов. Защищает от регрессий, где разные пути формирования сообщения дали бы разные строки.
- *Как:* inner for-loop по 3 source-сборкам. Воспроизводит `created` сценарий из `test_each_file_event_type_during_iteration_raises_with_correct_message` для каждого source. Проверяет регексп `match('Directory changed during transactional iteration: created at .*new\\.txt')` через `pytest.raises`.

### Regression / coverage гарантий

**75. `test_compile_excludes_helper_behaviorally_shared_between_traverse_and_handler`**
- *Property:* `Crawler._compile_excludes()` поведенчески идентичен в обоих code path: `_traverse` и `FilteredEventHandler` дают одинаковый результат для одного и того же exclude-pattern. Если в будущем кто-то fork'нет helper — этот тест сломается.
- *Как:* `crawler = Crawler(tmp_path, exclude=['*.tmp'])`. Создать в `tmp_path` файлы `'normal.txt'` и `'foo.tmp'`. (a) `assert Path(tmp_path)/'foo.tmp' not in list(crawler)` — `_traverse` исключает `*.tmp`. (b) В transaction touch'нуть `'foo.tmp'` (отфильтрованный) и `'sentinel.txt'` (разрешённый) через worker + barrier. По sentinel-pattern: `_change_alarm.wait()` → проверить `message` содержит `sentinel.txt`, не `foo.tmp`. Если бы handler использовал свой compile с другой логикой — `foo.tmp` мог бы trip-нуть первым и зафиксироваться в alarm. Поведенческое равенство = реальное переиспользование helper'а.

**76. `test_multi_path_crawler_transaction_triggers_on_change_in_any_path`**
- *Property:* `Crawler(path_a, path_b).transaction` наблюдает за **обоими** базовыми путями: изменение в любом из них срывает обход.
- *Как:* два временных каталога `a` и `b`. `crawler = Crawler(a, b)`. Два независимых подсценария в одном тесте:
  1. `with crawler.transaction as scope:` — worker через barrier трогает файл в `b` → `pytest.raises(TransactionalChangeError, match=match(...b/...))`.
  2. Аналогично, новый `with`, worker трогает файл в `a` → `pytest.raises(TransactionalChangeError, match=match(...a/...))`.
  Если бы был зашедулен только один путь — один из двух подсценариев бы не trip-нул.

### Default behavior regression

**77. `test_default_apply_without_transactional_yields_identical_results_to_plain_iteration_for_all_source_types`**
- *Property:* Гарантия из Context-секции: «поведение по умолчанию не меняется». `source.apply(fn)` (без `transactional=True`) даёт ровно тот же набор путей, что `for p in source: fn(p)`. Проверяется inner for-loop'ом по 3 source-типам.
- *Как:* inner for-loop по `Crawler`, `PythonCrawler`, `CrawlersGroup`:
  ```python
  for build_source in [
      lambda: Crawler(tmp_path),
      lambda: PythonCrawler(tmp_path),
      lambda: Crawler(tmp_path / 'sub') + Crawler(tmp_path, exclude=['sub/**']),
  ]:
      source = build_source()
      collected_apply = []
      source.apply(collected_apply.append)
      collected_iter = list(source)
      assert sorted(collected_apply) == sorted(collected_iter)
  ```

### Поведение при завершении программы

**78. `test_observer_thread_is_daemon_so_it_does_not_block_interpreter_exit`**
- *Property:* После `_start()`, `tc._observer.daemon is True`. Гарантирует, что watchdog-поток не будет удерживать интерпретатор от выхода, если `__exit__` пропущен.
- *Как:* `with crawler.transaction as scope: assert scope._observer.daemon is True`. Простой in-process assert, без subprocess.

**79. `test_process_exits_within_timeout_when_with_block_is_bypassed_via_sys_exit`**
- *Property:* Дочерний интерпретатор, который сделал `tc.__enter__()` и затем `sys.exit(0)` БЕЗ выхода из `with`, всё равно завершается за разумное время (благодаря `daemon=True` на observer'е и `atexit`-хуку).
- *Как:* через `suby.run` с `timeout=5.0` (запас под slow CI runner'ы вроде Windows GitHub Actions — process spawn + atexit + `observer.join(timeout=5.0)`). Пакет уже установлен через `pip install .` в CI, поэтому `from dirstree import Crawler` работает без манипуляций с `sys.path`:
  ```python
  import textwrap
  from suby import run

  script = textwrap.dedent(f'''
      import sys
      from dirstree import Crawler
      tc = Crawler({repr(str(tmp_path))}).transaction
      tc.__enter__()           # observer started, with-block NOT entered/exited
      sys.exit(0)              # graceful exit; __exit__ never runs
  ''')
  result = run(['python', '-c', script], timeout=5.0, catch_exceptions=True)
  assert result.returncode == 0
  assert not result.killed_by_token        # didn't hit timeout
  ```
  Если daemon=True не работает на текущей watchdog-версии — `suby.run` упадёт по `timeout` и тест провалится.

**80. `test_atexit_hook_calls_stop_when_with_block_is_bypassed`**
- *Property:* Если `__exit__` пропущен, зарегистрированный `atexit`-хук вызывает `_stop_safely_at_exit` → `_stop()` при завершении интерпретатора (доказывается через side-effect print).
- *Как:* spy на `TransactionalCrawler._stop` в child-процессе. Пакет установлен через CI, импорты прямые:
  ```python
  script = textwrap.dedent(f'''
      import sys
      from dirstree import Crawler
      from dirstree.crawlers.transactional.crawler import TransactionalCrawler
      original_stop = TransactionalCrawler._stop
      def spy(self):
          print('STOP_CALLED', flush=True)
          return original_stop(self)
      TransactionalCrawler._stop = spy
      tc = Crawler({repr(str(tmp_path))}).transaction
      tc.__enter__()
      sys.exit(0)
  ''')
  result = run(['python', '-c', script], timeout=5.0, catch_exceptions=True)
  assert result.returncode == 0
  assert 'STOP_CALLED' in result.stdout
  ```

**81. `test_atexit_hook_is_actually_deregistered_after_normal_stop_via_sentinel_pattern`**
- *Property:* После нормального завершения `with`-блока (`_stop()` вызван) `atexit.unregister(_stop_safely_at_exit)` **реально** удалил хук (не просто был вызван). Доказывается через subprocess + sentinel: после выхода из `with` регистрируется фоновый sentinel-handler через `atexit.register`; на shutdown'е sentinel срабатывает, а `_stop_safely_at_exit` НЕ срабатывает повторно (`_stop()` через спай вызывается **ровно один раз** — внутри `with`-блока).
- *Как:* subprocess через `suby.run` с capture stdout:
  ```python
  script = textwrap.dedent(f'''
      import sys, atexit
      from dirstree import Crawler
      from dirstree.crawlers.transactional.crawler import TransactionalCrawler
      stop_count = [0]
      original_stop = TransactionalCrawler._stop
      def spy(self):
          stop_count[0] += 1
          return original_stop(self)
      TransactionalCrawler._stop = spy
      with Crawler({repr(str(tmp_path))}).transaction as scope:
          pass                                   # _stop called once here via __exit__
      atexit.register(lambda: print(f'SENTINEL stop_count={{stop_count[0]}}', flush=True))
      sys.exit(0)                                # atexit fires: sentinel prints; _stop_safely_at_exit should NOT re-fire
  ''')
  result = run(['python', '-c', script], timeout=5.0, catch_exceptions=True)
  assert result.returncode == 0
  assert 'SENTINEL stop_count=1' in result.stdout    # _stop was called exactly once
  ```
  Это **effect-test**: sentinel наблюдает реальное состояние атексит-чейна. Если `unregister` молча failed, sentinel зафиксирует `stop_count=2`.

**82. `test_stop_safely_at_exit_emits_resource_warning_when_underlying_stop_raises`**
- *Property:* `_stop_safely_at_exit` ловит исключения из `_stop()` и эмитит `ResourceWarning` с подробностями (не silent pass). Симметрично существующему `warnings.warn` в `_stop()` при таймауте join'а.
- *Как:* **не используем `with`** — иначе `__exit__` → `_stop()` → broken_stop поднимет повторное исключение из контекстника. Тест полностью на ручных `_start`/`_stop`-вызовах с явным `try/finally`-cleanup'ом monkeypatch'а:
  ```python
  tc = crawler.transaction
  tc._start()
  try:
      def broken_stop():
          raise RuntimeError('synthetic stop failure')
      tc._stop = broken_stop                                 # instance-level shadow
      with pytest.warns(ResourceWarning, match='synthetic stop failure'):
          tc._stop_safely_at_exit()                          # exception suppressed → warning emitted
      # _active остался True (broken_stop never actually transitioned state)
      assert tc._active is True
  finally:
      del tc._stop                                           # restore class method
      if tc._active is True:
          tc._stop()                                         # clean shutdown
  ```

**83. `test_stop_emits_resource_warning_when_observer_join_times_out`**
- *Property:* Существующий `warnings.warn(ResourceWarning)` в `_stop()` реально срабатывает, когда `observer.join(timeout=5.0)` не дождался смерти потока. Покрытие невидимой ветки.
- *Как:* **не используем `with`** — `__exit__` вызовет `_stop()` повторно после нашего ручного `_stop()`, что даст `TransactionInactiveError`:
  ```python
  tc = crawler.transaction
  tc._start()
  monkeypatch.setattr(tc._observer, 'join', lambda timeout=None: None)
  monkeypatch.setattr(tc._observer, 'is_alive', lambda: True)
  with pytest.warns(ResourceWarning, match=match('TransactionalCrawler observer thread did not terminate within 5s of stop(). This is a watchdog/OS-layer bug; the thread is being left as a daemon.')):
      tc._stop()
  # tc._active is now False; no further cleanup needed.
  ```

### Stress-сьют (opt-in)

Stress-тесты живут в основном `tests/units/crawlers/transactional/test_crawler.py`, помечены `@pytest.mark.stress`. Маркер регистрируется в `pyproject.toml`. Базовый прогон `pytest` исключает их через `addopts = ["-m", "not stress"]` (массив toml — устойчивее к кавычкам, чем строка); для запуска только stress-тестов — `pytest -m stress`.

Fixture `cpu_pressure` поднимает N=`cpu_count()` `multiprocessing.Process`-воркеров, каждый крутит `hashlib.sha256(b'x' * 4096).digest()` в петле (real CPU-bound workload, не `while True: pass`). После теста — `stop.set()` + `join(timeout=2.0)` + `terminate()` для зависших.

Stress-тесты применяют fixture как обычный pytest-параметр функции: `def test_xxx_under_cpu_pressure(cpu_pressure, tmp_path): ...` — pytest сам поднимет cpu_pressure до теста и завершит после. Сам код теста идентичен оригиналу — fixture только создаёт фоновую нагрузку, не вмешивается в логику.

Тесты:
- `test_creation_during_iteration_under_cpu_pressure_still_trips` — переиспользует сценарий `test_each_file_event_type_during_iteration_raises_with_correct_message` (`created` event during iteration) с дополнительным `cpu_pressure`-параметром. Прогоняется N раз через **inner for-loop** (`for repeat in range(20): ...`) — соответствует CLAUDE.md convention (без `@pytest.mark.parametrize` декоратора). Цель: выявить недокументированные timing-зависимости. Тест по-прежнему детерминирован через barrier — CPU pressure только увеличивает реальные задержки. Если один из repeat-итерейшнов упадёт, нет per-iteration isolation — это осознанный compromise проекта.
- `test_two_threads_under_cpu_pressure_remain_isolated` — переиспользует `test_two_threads_each_with_own_transaction_are_fully_isolated` с `cpu_pressure`-параметром, N раз.
- `test_apply_transactional_under_cpu_pressure_still_trips_correctly` — переиспользует `test_apply_transactional_true_raises_on_change_and_stops_calling_function` с `cpu_pressure`-параметром.

Команда запуска:
```
pytest -m stress tests/units/crawlers/transactional/test_crawler.py
```

### Python invariant tests

Тесты инвариантов в `tests/python/interpreter/` (свойства CPython) и `tests/python/dependencies/` (свойства внешних зависимостей).

#### `tests/python/interpreter/test_atexit.py`

**`test_atexit_fires_callbacks_on_sys_exit`**
- *Property:* `atexit`-callbacks вызываются при `sys.exit(0)` (нормальный выход через `SystemExit`).
- *Docstring (мandatory, подробный):* (а) на что мы опираемся: `_stop_safely_at_exit`, зарегистрированный в `TransactionalCrawler._start()` через `atexit.register`, должен вызваться при любом нормальном завершении интерпретатора (включая `sys.exit()`); (б) что сломается у нас, если инвариант перестанет работать: graceful shutdown сломается — если пользователь обошёл `with`-блок и вызвал `sys.exit()`, observer-thread не остановится корректно, в worst case ProcessExit повиснет (не повиснет благодаря `daemon=True`, но без atexit `_stop()` не вызовется и в логах не будет diagnostic warning'а). Реализация теста — subprocess через `suby.run`.
- *Как:* subprocess регистрирует sentinel-handler, делает `sys.exit(0)`, parent проверяет stdout:
  ```python
  script = textwrap.dedent('''
      import sys, atexit
      atexit.register(lambda: print('ATEXIT_FIRED', flush=True))
      sys.exit(0)
  ''')
  result = run(['python', '-c', script], timeout=5.0, catch_exceptions=True)
  assert 'ATEXIT_FIRED' in result.stdout
  ```

**`test_atexit_does_not_fire_callbacks_on_os_exit`**
- *Property:* `atexit`-callbacks **НЕ** вызываются при `os._exit(0)` (mainline bypass). Это контр-инвариант: подтверждает, что `os._exit` ломает наш cleanup, и пользователь не должен его использовать в активной транзакции.
- *Как:*
  ```python
  script = textwrap.dedent('''
      import os, atexit
      atexit.register(lambda: print('SHOULD_NOT_FIRE', flush=True))
      os._exit(0)
  ''')
  result = run(['python', '-c', script], timeout=5.0, catch_exceptions=True)
  assert 'SHOULD_NOT_FIRE' not in result.stdout
  ```

**`test_atexit_handler_can_call_unregister_on_itself_during_atexit_iteration`**
- *Property:* `atexit.unregister(fn)` вызванный **внутри** `atexit`-handler'а (когда CPython итерирует callback-список) не corrupt'ит чейн. Это инвариант, на который опирается путь `_stop_safely_at_exit → _stop() → atexit.unregister(self._stop_safely_at_exit)`. Если CPython однажды поменяет внутреннюю структуру списка callbacks, путь сломается.
- *Как:*
  ```python
  script = textwrap.dedent('''
      import atexit
      def self_unregistering():
          atexit.unregister(self_unregistering)
          print('SELF_UNREG_RAN', flush=True)
      def sentinel():
          print('SENTINEL', flush=True)
      atexit.register(self_unregistering)
      atexit.register(sentinel)
      # Implicit sys.exit
  ''')
  result = run(['python', '-c', script], timeout=5.0, catch_exceptions=True)
  assert 'SELF_UNREG_RAN' in result.stdout
  assert 'SENTINEL' in result.stdout
  ```

#### `tests/python/interpreter/test_atomicity.py`

**`test_plain_attribute_write_is_visible_to_concurrent_readers_within_short_time`**

- *Property:* Запись plain Python-attribute одним потоком становится **видимой другим потокам** в течение разумного времени (микросекунды до миллисекунд) **без явного lock'а/Event'а** на стороне читателей. На CPython с GIL это гарантируется serialization GIL'а; на free-threading 3.13t/3.14t — атомиками с (предполагается) seq_cst semantics для object-reference reads/writes (PEP 703). Если этот инвариант сломается в какой-то будущей версии Python — наш `ConditionToken(lambda: self._active is True)` в `TransactionalCrawler.go()` перестанет работать корректно: писатель (`_stop()`) запишет `_active=False` под `_lifecycle_lock`, но читатели (iterator-потоки) могут продолжать видеть `True` неограниченно долго, и `TransactionStoppedDuringIterationError` никогда не сработает. Это превратится в silent data corruption — workers продолжат итерировать на мёртвом observer'е без какой-либо ошибки.

- *Docstring (мandatory, подробный):* объяснить (а) на что опирается реализация: ссылка на `cond_alive` в `TransactionalCrawler.go()`, (б) что значит падение этого теста: «инвариант Python о visibility plain-attribute write сломан на этой версии Python; нужно срочно заменить чтение `self._active` в `cond_alive`-lambda на чтение через `threading.Event` (например, добавить `_stop_event = threading.Event()` рядом с `_active`, обновлять оба под lock'ом в `_stop()`, читать `_stop_event.is_set()` в lambda)».

- *Как (детерминированно, без флэка):*
  ```python
  import threading, time
  
  class Holder:
      def __init__(self):
          self.flag = True

  holder = Holder()
  N = 8                                                      # number of reader threads
  barrier = threading.Barrier(N + 1)                          # readers + main
  saw_change = [False] * N
  
  def reader(i):
      barrier.wait()                                          # rendezvous start
      deadline = time.monotonic() + 2.0                       # max wait 2s
      while time.monotonic() < deadline:
          if holder.flag is False:                            # plain attribute read
              saw_change[i] = True
              return
  
  threads = [threading.Thread(target=reader, args=(i,)) for i in range(N)]
  for t in threads: t.start()
  barrier.wait()                                              # release all readers
  holder.flag = False                                         # single plain write
  for t in threads: t.join()
  
  assert all(saw_change), (
      f'Plain attribute write was not visible to concurrent readers within 2s. '
      f'saw_change={saw_change}. '
      f'Implication: TransactionalCrawler._stop() signal would not reach iterating '
      f'threads on this Python version. Need to migrate _active read to threading.Event.'
  )
  ```
  - Дедлайн 2 секунды — заведомо больше любой разумной cache-coherency latency. Если write реально не visible (broken invariant) — readers упрутся в timeout, тест fail'нет с информативным сообщением.
  - На текущем CPython (любой версии, GIL или free-threading 3.13t/3.14t) тест должен проходить мгновенно (visibility — наносекунды).

#### `tests/python/dependencies/test_cantok.py`

**`test_composite_token_cancellation_exposes_subtoken_identity_in_exc_token`**

- *Property:* `cantok.CancellationError`, поднятое при cancellation **композитного** токена (`token_a + token_b`), несёт в `exc.token` identity именно того **leaf**-токена, который реально сработал, а не identity composite. На этом стоит вся диспетчеризация в `TransactionalCrawler.go()`: мы делаем `is`-сравнение `exc.token is cond_alive` / `exc.token is cond_no_alarm` чтобы понять причину отмены. Если cantok решит возвращать composite (или, скажем, корневой `token + cond_alive + cond_no_alarm`), наша disambiguation сломается: оба `is`-чека дадут False, мы пропустим отмену в `raise`-ветку и пользователь получит `CancellationError` вместо `TransactionalChangeError` / `TransactionStoppedDuringIterationError`.

- *Docstring (обязательный, подробный):* объяснить (а) что от cantok мы ждём leaf-identity в `exc.token`, (б) что наша имплементация **`TransactionalCrawler.go()`** (см. соответствующий код-скелет) полагается на это для disambiguation, (в) при падении этого теста сломается весь error-routing в transactional-режиме: пользователи получат raw `CancellationError` вместо доменного исключения, без указания причины (изменение в директории / остановка во время итерации) — silent semantic corruption.

- *Как:*
  ```python
  from cantok import ConditionToken, SimpleToken, CancellationError
  
  fired = SimpleToken()
  not_fired = ConditionToken(lambda: True)            # never cancelled
  composite = SimpleToken() + fired + not_fired
  fired.cancel()
  
  with pytest.raises(CancellationError) as exc_info:
      composite.check()
  assert exc_info.value.token is fired, (
      f'Expected exc.token to be the fired sub-token (identity), '
      f'got {exc_info.value.token!r}. '
      f'Implication: TransactionalCrawler.go() disambiguation by `is`-comparison '
      f'will fail; users will see raw CancellationError instead of '
      f'TransactionalChangeError / TransactionStoppedDuringIterationError.'
  )
  ```

**`test_custom_raise_on_cancel_wraps_original_via_explicit_cause`**

- *Property:* Если у `cantok`-таского объекта (например `Crawler` с `raise_on_cancel=CustomError`) установлено custom исключение, при cancellation cantok делает `raise CustomError(...) from original_cancellation_error` — т.е. оригинальный `CancellationError` доступен как `exc.__cause__` (НЕ `exc.__context__` или какой-то другой механизм). На этом стоит наша поддержка custom `raise_on_cancel` в `TransactionalCrawler.go()`: мы делаем `cancellation = exc if isinstance(exc, CancellationError) else (exc.__cause__ if isinstance(exc.__cause__, CancellationError) else None)`. Если cantok использует `__context__` вместо `__cause__` — наш чек пропустит, и trip от нашего condition_token (через custom raise_on_cancel) не сконвертируется в `TransactionalChangeError`, пользователь получит raw `CustomError` вместо нашего доменного исключения.

- *Docstring (обязательный, подробный):* (а) на что мы опираемся — `__cause__`-цепочка через `raise X from Y`; (б) где это используется — `TransactionalCrawler.go()` except-блок (см. код-скелет); (в) при падении теста — поддержка custom `raise_on_cancel` сломается, тест `test_custom_raise_on_cancel_disambiguates_token_sources_via_cause` будет флакать или давать неверный результат.

- *Как:*
  ```python
  from cantok import SimpleToken
  
  custom_token = SimpleToken(cancelled=True, raise_on_cancel=ValueError)
  with pytest.raises(ValueError) as exc_info:
      custom_token.check()
  # cantok must use `raise ValueError(...) from original_cancellation_error`
  assert isinstance(exc_info.value.__cause__, CancellationError), (
      f'Expected exc.__cause__ to be the original CancellationError. '
      f'Got __cause__={exc_info.value.__cause__!r}, __context__={exc_info.value.__context__!r}. '
      f'Implication: TransactionalCrawler.go() __cause__-inspection will miss '
      f'condition_token-triggered cancellations when source has custom raise_on_cancel; '
      f'trip from filesystem change will surface as raw user exception instead of '
      f'TransactionalChangeError. Test test_custom_raise_on_cancel_disambiguates_token_sources_via_cause will likely flake or fail.'
  )
  ```

#### `tests/python/dependencies/test_watchdog.py`

Тесты инвариантов `watchdog`-API, на которые опирается наша реализация. Любой из них падает → значит, в новой версии watchdog поменялся контракт; нужно осознанно пересмотреть наш код. По одному тесту на каждый important aspect.

**`test_event_type_strings_are_exact_literals`**
- *Что:* `watchdog.events.FileSystemEvent.event_type` для разных типов событий принимает **строго** значения `'created'`, `'deleted'`, `'modified'`, `'moved'`. На эти строки прямо опирается формирование `TransactionalChangeError.message` в `ChangeAlarm.set_first`.
- *Что сломается:* если watchdog поменяет нейминг (например, `'create'` без d), сообщения в исключении станут wrong, тест `test_each_file_event_type_during_iteration_raises_with_correct_message` упадёт на match-проверках.
- *Как:* импортировать `FileCreatedEvent`, `FileDeletedEvent`, `FileModifiedEvent`, `FileMovedEvent`. Создать инстансы. Проверить `event.event_type == 'created'` и т.д. для каждого. Можно через inner for-loop с парами `(Class, expected_string)`.

**`test_observer_schedule_and_event_dispatch_to_handler`**
- *Что:* `watchdog.observers.Observer().schedule(handler, path, recursive=True)` подписывает handler на события в `path`. После `observer.start()`, мутации в `path` доставляются как вызовы `handler.on_any_event(event)` (с правильно установленными `event.src_path`, `event.event_type` и т.д.).
- *Что сломается:* основной механизм нашей реализации — handler не получит событий, transactional перестанет работать.
- *Как:* `tmp_path`. Кастомный handler с `received = []`; на `on_any_event(event)` добавляет `event.event_type, event.src_path` в список. `Observer().schedule(handler, tmp_path, recursive=True); observer.start(); (tmp_path/'foo.txt').touch(); event_arrived.wait(timeout=2.0); observer.stop(); observer.join()`. Проверить, что в `received` есть `('created', str(tmp_path/'foo.txt'))`.

**`test_observer_single_dispatcher_processes_events_sequentially`**
- *Что:* У одного `Observer` один dispatcher-поток; события из одного `emitter` обрабатываются handler'ом **последовательно** (не параллельно). На этом стоит наш **sentinel-pattern** в тестах `test_filtered_event_does_not_trip_iteration`, `test_exclude_pattern_matches_deeply_nested_paths_in_handler`, `test_multiple_exclude_patterns_all_apply_in_handler`, `test_multiple_extensions_all_recognized_in_handler`, `test_directory_event_is_ignored_when_only_files_true`.
- *Что сломается:* если watchdog в будущем перейдёт на multi-threaded dispatch — sentinel-pattern сломается: ignored и sentinel могут быть обработаны параллельно, и first-wins недетерминирован.
- *Как:* handler регистрирует `(event.event_type, time.monotonic())` в shared-list под lock'ом. Worker делает 10 последовательных touch'ей. После `wait(timeout=2.0)` для последнего event'а: проверить, что timestamps монотонно возрастают (sequential). Если возникнут одновременные timestamps или порядок нарушен — dispatcher не sequential.

**`test_filesystem_event_handler_subclass_callable`**
- *Что:* `class MyHandler(FileSystemEventHandler): def on_any_event(self, event): ...` — корректный subclass, watchdog вызывает `on_any_event` для всех типов событий.
- *Что сломается:* если watchdog уберёт `on_any_event` или поменяет сигнатуру — наш handler перестанет получать события.
- *Как:* минимальный smoke-тест: подкласс с `on_any_event`, schedule на tmp_path, touch файла, ожидаем вызов.

**`test_observer_start_stop_join_lifecycle`**
- *Что:* Lifecycle: `Observer()` → `schedule()` → `start()` → ... → `stop()` → `join()` без таймаута завершается за разумное время на чистом сценарии (без зависших ивентов).
- *Что сломается:* если watchdog изменит lifecycle-API или станет вешаться на `join()` без timeout — наш `_stop()` будет блокировать навечно.
- *Как:* создать Observer, schedule на tmp_path, start, stop, `observer.join(timeout=5.0)` — `assert not observer.is_alive()`. Если зависло — timeout сработает, тест fail'нет с понятным сообщением.

### Платформенные skip-ы

**По умолчанию — никаких preemptive skip'ов.**

**Правило**: если в момент имплементации какой-то тест реально падает или флакает на конкретной платформе/сборке — **сначала обращаемся к пользователю**, обсуждаем причину и решение, и только потом добавляем `pytest.mark.skipif(...)` с явной ссылкой на конкретный issue/bug-репорт. Тихий skip без обсуждения — запрещён.

Известные платформенные особенности (без skip):
- Windows ReadDirectoryChangesW шлёт дубликаты `modified`-событий ([watchdog #346](https://github.com/gorakhargosh/watchdog/issues/346)) — `ChangeAlarm` first-wins иммунен.
- Free-threading 3.14t: статус watchdog'а публично не задокументирован — проверить в момент имплементации.

## `issue_self.md`

Создать в корне проекта со следующим содержимым (формат GitHub issue, английский):

```
# FileNotFoundError leaks from `rglob` during concurrent file deletion

## Description

`Crawler._traverse()` iterates the filesystem via `pathlib.Path.rglob('*')`. `rglob` first reads a directory listing (a snapshot of names) and then yields `Path` objects one by one. For each yielded entry our code calls `child_path.is_file()` and/or `child_path.is_dir()` — both of which internally invoke `os.stat(...)`.

If a file or directory is removed between the moment its name appears in the listing and the moment `is_file()`/`is_dir()` is called on it, the underlying `stat()` raises **`FileNotFoundError`**. This exception propagates out of `_traverse()` and breaks iteration with an error unrelated to the user's API.

## Reproduction

Create ~1000 files in a temp dir, start a background thread deleting them, iterate via `Crawler(root)`. Under concurrent deletion, `child_path.is_file()` may raise `FileNotFoundError` for entries that vanished between rglob listing and stat. Full repro: race a `Crawler(root)` iteration against `Thread(target=deleter).start()` where `deleter` unlinks files in parallel.

## Impact

- Affects all `Crawler` uses, not only the transactional mode.
- Becomes more visible in `TransactionalCrawler` because users of that mode explicitly expect the directory to be mutated during iteration.
- In transactional mode, the user expects `TransactionalChangeError`. Instead they may receive a bare `FileNotFoundError` (raised before our `ConditionToken` gets a chance to trip).

## Suggested fix

Two reasonable options:

1. **Inside `Crawler._traverse`** — wrap the `is_file()`/`is_dir()` calls (and any subsequent `stat`-dependent checks) in `try/except FileNotFoundError: continue`. Silently skip vanished entries. This is the canonical fix for any filesystem traversal under concurrent mutation.
2. **Inside `TransactionalCrawler.go`** — additionally catch `FileNotFoundError`; if `self._change_alarm.is_alarmed()`, raise `TransactionalChangeError` (since the missing file is the change we were detecting). Otherwise re-raise.

Option (1) is preferred — it fixes the root cause for all `Crawler` callers.

## Why deferred

Pre-existing bug, not introduced by transactional mode. Out of scope for the transactional MVP. Logged as a follow-up; should be addressed in a separate change focused on `Crawler` itself.
```

## Документация

README — см. описание в «Структура файлов» (изменяемые) выше. Дополнительно:
- Docstring `Crawler` — параграф о `transaction` и `apply(transactional=True)`.
- Docstring `TransactionalCrawler` — поведение, ограничения, race window.

## Известные ограничения (документируем явно)

1. **Платформенная задержка доставки событий**. OS notification mechanisms имеют inherent latency (inotify < 1мс, RDCW ~1–3мс, FSEvents ~10мс — параметр `latency` watchdog'а на macOS) между мутацией и доставкой handler'у. Если изменение случилось *сразу перед* `_check_token` — отдадим ещё один path до доставки события; следующий yield сорвётся. Best-effort гарантия.
2. `pathlib.Path.rglob('*')` делает stat per-entry → возможен `FileNotFoundError` из source. Существующая проблема всего `Crawler`, в transactional-режиме обостряется. Подробно описана в `issue_self.md` как отдельная задача, выходящая за рамки MVP. **Прямого тестового покрытия для resilience против этого нет** — оно намеренно не тестируется в данной фиче: писать тест на «надежно ловит FileNotFoundError» означает писать тест на отсутствующий fix. Существующее покрытие — **negative**: deleted/moved-сценарии в `test_each_file_event_type_*` намеренно избегают триггера бага через careful ordering (явно задокументировано в docstring теста). Когда баг будет исправлен в отдельной задаче, positive-тесты на устойчивость к этому сценарию появятся там же.
3. **`__cause__`-цепочка раскручивается на один уровень.** В `go()`-except проверяем `exc.__cause__ is CancellationError` (для случая custom `source.raise_on_cancel`). Многоуровневая обёртка (>1) сломает чек — пользователь получит raw outer exception вместо `TransactionalChangeError`. На практике cantok делает один уровень `raise X from Y`, так что типово это не возникает.

## Шаг до начала имплементации

**Скопировать сам этот план в `docs/plans/transactions.md`** (создать директорию `docs/plans/` если её нет). Это якорь для рецензента и для будущих изменений: при последующих refactor'ах фичи будет видно, какой именно дизайн был согласован. Файл commit'ится в репозиторий вместе с самой имплементацией в первом же commit'е feature-ветки.

## Verification

Эти шаги нужно выполнить **последовательно в конце имплементации**, до того как считать задачу выполненной:

1. **Проверить, что `docs/plans/transactions.md` существует** (создан как первый шаг имплементации, см. секцию «Шаг до начала имплементации» выше) и что его содержимое совпадает с одобренным планом. Без этого якоря рецензент не сможет соотнести имплементацию с design-документом.
2. Полный suite: `pytest` — должен проходить, включая `tests/units/crawlers/transactional/test_crawler.py`. Stress-сьют по умолчанию исключён через `addopts = ["-m", "not stress"]`.
3. 100% coverage gate из CI (как в CLAUDE.md):
   ```
   coverage run --source=dirstree --omit="*tests*" -m pytest --cache-clear --assert=plain && coverage report -m --fail-under=100
   coverage run --branch --source=dirstree --omit="*tests*" -m pytest --cache-clear --assert=plain && coverage report -m --fail-under=100
   ```
   Ветка «watchdog отсутствует» покрывается через monkeypatch на `TransactionalCrawler._load_watchdog`.
4. Lint/type:
   ```
   ruff check dirstree
   ruff check tests
   mypy --strict dirstree
   mypy tests --exclude typing
   ```
5. **Stress-сьют — обязательный шаг**, не «вручную если хочется». Выполняется **отдельно от шага 2** (`pytest` в дефолтной конфигурации исключает stress через `addopts = ["-m", "not stress"]`); порядок не важен (шаги 2 и 5 независимы). Прогнать перед merge, чтобы убедиться, что race-сценарии стабильны под CPU pressure:
   ```
   pytest -m stress tests/units/crawlers/transactional/test_crawler.py
   ```
   Если хоть один прогон с stress даёт таймаут или ChangeError не приходит — нужно разобраться, а не игнорировать.
6. Smoke вручную:
   ```python
   from dirstree import Crawler
   c = Crawler('.', exclude=['.git/**', '__pycache__/**'])
   with c.transaction as scope:
       for p in scope:
           print(p)
   # В соседнем терминале — `touch foo.txt` → TransactionalChangeError.
   ```
