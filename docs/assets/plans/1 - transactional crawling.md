# Transactional crawling for `dirstree`

## Context

Сегодня `Crawler` обходит директорию как генератор, без гарантии консистентности. `freeze=True` (snapshot заранее) лишь смещает проблему: snapshot собирается не атомарно, и изменения после него неотличимы от отсутствия.

Цель — опциональный режим, в котором любое значимое изменение в наблюдаемой области во время обхода прерывает итерацию исключением. Реализация — через `watchdog` (фоновый поток событий ФС) + `cantok.ConditionToken`, который встраивается в существующую токен-цепочку. Поведение по умолчанию не меняется. Детальные границы значимых событий, ошибок и многопоточности описаны в «Поведенческом контракте».

Ключевые параметры режима:
- События: **created + deleted + moved + modified**.
- Те же фильтры (`extensions`, `exclude`, `filter`, `only_files`), что у краулера, применяются к событиям, кроме watched-base boundary: delivered `deleted` с boundary endpoint и `moved` с boundary endpoint значимы после успешной классификации, а ordinary `created`/`modified` watched base игнорируются.
- `watchdog` — **опциональная** зависимость через `pip install dirstree[transactional]`.
- Имя — `transaction`. API: `apply(transactional=True)` и `with crawler.transaction as scope:`.

## Термины

Термин `trip` в плане: событие признано значимым и вызывает `ChangeAlarm.set_first(...)`; дальше ближайшая проверка token/final-check превращает это в `TransactionalChangeError`.

`yield-фильтры` = leaf-level event pipeline по snapshot-конфигурации (`extensions`, `exclude`, `filter`, `only_files`); он не запускает `_traverse()` заново и не проверяет существование path через `is_file()`/`is_dir()`. В handler `only_files` вычисляется только по `event.is_directory`: при `only_files=True` non-boundary directory events игнорируются. Group-level dedup сюда не входит.

`source-order guarantee` = обещание, что message выберется по порядку `Crawler.paths` или leaf'ов `CrawlersGroup`. Такого обещания нет: message определяет первое доставленное watchdog-событие, которое стало значимым после boundary/yield-filter обработки, в том числе у одного multi-path `Crawler`. Это относится к delivery order событий/handlers; `_bases` order всё равно используется как tie-break при классификации одного уже доставленного event внутри одного handler invocation.

`first-wins` = `ChangeAlarm` записывает только первое значимое событие; последующие события не меняют message.

`leaf` = конечный `Crawler` после разворачивания `CrawlersGroup`/вложенных TC. `watched base` = существующая directory base из leaf `.paths`, на которую вызван `observer.schedule(..., recursive=True)`. Для symlink directory base boundary matching идёт по resolved target; delete/move самого symlink path не гарантируется. `parent-watch` = дополнительная watchdog-подписка на `Path(watched_base).parent`. `boundary endpoint` = endpoint события, чей resolved path равен precomputed `resolved_base` watched base; для non-moved event это `src_path`, для `moved` — `src_path` и/или `dest_path`. `observed area` = watched base плюс recursive descendants; для symlink descendants принадлежность определяется handler-time/current-FS `Path(event_path).resolve(strict=False)`, а не состоянием symlink target на момент OS-event и не текстовым prefix path; это только правило классификации уже доставленных событий, TC не гарантирует, что watchdog recursive watch следует по symlink-директориям/target'ам. Кэш прежних symlink targets и lexical fallback к `crawler_base` не делать: результат `resolve(strict=False)` используется как есть, и если `relative_to(resolved_base)` не проходит, endpoint считается `outside`. Если endpoint подходит под несколько watched bases одного handler, значимость считается только относительно выбранного deepest `resolved_base`; fallback к более широкому base не делается. Descendant events идут через yield-фильтры, а watched-base boundary events — через отдельную ветку, где значимы только delivered `deleted` с boundary endpoint и `moved` с boundary endpoint.

`crawler-coordinate path` = `crawler_base / relative_tail`, где "та же форма, что у `_traverse()`" означает только relative/absolute форму `crawler_base`. Для symlink descendants `relative_tail` намеренно берётся от resolved-target path (`resolved_event.relative_to(resolved_base)`), без попытки сохранить lexical path через symlink.

## Зависимости и общие правила

- Новые dev: `locklib==0.0.23` (`LockTraceWrapper`) и `suby==0.0.12` (`suby.run` для shutdown-тестов).
- Optional runtime extra: `watchdog` (`Observer.schedule/start/stop/join`), устанавливается через `dirstree[transactional]`.
- Существующие зависимости используем как есть: `cantok`, `pathspec`, `printo`, `sigmatch`, `full_match`.

**Язык кода**: **все строки внутри исходного кода, включая тесты** (error messages, docstrings, комментарии) — **на английском**. Русские формулировки в плане не являются строками для кода. Соответственно тесты ассертят на английские строки через `match=match(...)`.

## Подход (high-level)

Один класс — `TransactionalCrawler(AbstractCrawler)`. Он же сам себе context manager. Далее `TC` = `TransactionalCrawler`.

`AbstractCrawler.transaction` — `@property` (не `cached_property`). Обычные источники (`Crawler`, `PythonCrawler`, `CrawlersGroup`) на каждый доступ возвращают новый `TransactionalCrawler(self)`. Активный `TransactionalCrawler.transaction` возвращает `self`; неактивный TC (fresh/stopped) кидает `TransactionInactiveError` с единым сообщением. `CrawlersGroup` не переопределяет property и возвращает `TransactionalCrawler(group)` без рекурсивного оборачивания children.

Cancellation встраивается через `cantok.ConditionToken`: один internal token следит за `_active`, второй — за `ChangeAlarm`. Подробный порядок pre-source/final-check, priority и limitation cantok identity описан в «Поведенческом контракте».

**Централизованная схема обработки событий**:
- Один Observer и один shared `ChangeAlarm` на TC.
- `_observer` через `unwrap_to_crawlers(source)` разворачивает `CrawlersGroup` и вложенные TC до листовых `Crawler`-ов.
- На каждый leaf создаётся отдельный `FilteredEventHandler` с собственными фильтрами; `observer.schedule(..., recursive=True)` вызывается только для каждого existing directory base, поэтому handler с `scheduled_bases == ()` создаётся, но не schedule'ится. Scheduled handlers пишут в shared `ChangeAlarm`.

`TransactionalCrawler`:
- Хранит ссылку на источник, `_active`, `_observer`, `_change_alarm`.
- `__enter__` → `self._start()`, `__exit__` → `self._stop()`; документированный путь — `with crawler.transaction as scope:`. Полный контракт lifecycle'а (ручные `_start()`/`_stop()` в тестах, поведение `__exit__` при исключении из тела `with`, проверки невалидных переходов) — в «Поведенческом контракте».
- `go()` — единый код-путь для одиночного Crawler и для CrawlersGroup. Логика разворачивания листьев — внутри cached_property `_observer`.
- Публичные методы итерации (`go`, `__iter__`, `apply`) доступны только при активной транзакции; точный порядок validation/lifecycle/callable/check ошибок описан в «Поведенческом контракте» и код-скелете `TC.apply`.

Thread-safety и lifetime Observer: новый TC на каждый доступ делает параллельные `with c.transaction as scope:` независимыми по TC-owned ресурсам; Observer живёт всю транзакцию, не per-`go()`. Детали общих source/filter state, nested `with` и `scope.transaction` — в «Поведенческом контракте».

## Примеры API

```python
from dirstree import Crawler

crawler = Crawler('src/', extensions=['.py'], exclude=['build/**'])

with crawler.transaction as scope:
    for path in scope:
        process(path)

crawler.apply(process, transactional=True)
```

## Поведенческий контракт

Базовые границы:
- Значимое событие = событие проходит yield-фильтры; доставленный watchdog'ом `deleted` с boundary endpoint или `moved` с хотя бы одним boundary endpoint всегда значимы после успешной endpoint-классификации (`resolved_event == resolved_base`, не lexical/string equality с `crawler_base`).
- Normalization errors не ловятся в handler, уходят в watchdog dispatcher thread и не пробрасываются через `TC.go()`/`__exit__`. `Exception` из user `filter` в watchdog handler тоже trip'ит транзакцию (`KeyboardInterrupt`/`SystemExit` не ловим). Любое non-cancellation исключение из `source.go()` пробрасывается как есть; `TC.go()` конвертирует только cancellation от внутренних токенов и clean post-loop/final alarm.
- Многопоточность: отдельные `with c.transaction as scope:` независимы по TC-owned ресурсам; несколько worker'ов внутри одного `scope` безопасны как одна общая транзакция, если каждый создаёт свой независимый iterator из `scope` и владелец scope не выходит из `with` до завершения этих worker'ов. Каждый такой iterator делает полный обход source; `scope` не является shared work queue. Shared source/source.token/filter callable и mutable state filter'а не изолируются и не сериализуются TC; shared generator usage не детектируем и не запрещаем, locks/guards не добавляем, остаётся стандартное поведение Python generator.

### Cancellation ordering и доставка событий

`ConditionToken` из cantok — poll-based примитив. Лямбда, переданная в `ConditionToken(predicate)`, вызывается на проверках `token.check()` / `bool(token)` / `_check_token` до первого `True`; с default `caching=True` после первого `True` cancelled-state может кэшироваться без повторного вызова predicate. Мы опираемся только на monotonic False→True значения predicate'ов `self._active is not True` и `self._change_alarm.is_alarmed()`; raw `_active` остаётся lifecycle-state `None → True → False`. Минимальная гарантия от `Crawler.go`: `_check_token(merged)` будет выполнен до следующего yielded path; текущий `_traverse()` также проверяет token после каждого просмотренного `child_path`, включая отфильтрованные. `TC.go()` не добавляет отдельную pre-yield проверку поверх `source.go()` ни для `cond_changed`, ни для `cond_stopped`: если alarm или stop случился после последнего `_check_token`, текущий path может пройти, а следующий check или post-loop check внутри того же `TC.go()` поднимет ошибку.

Контракт **«best-effort на стороне ОС, детерминирован на стороне нашей логики»**; детали latency — в «Известных ограничениях».

- Как только событие доставлено в наш handler и записано в `ChangeAlarm`, `ConditionToken` видит его на следующем `_check_token`; final-check в `go()`/`__exit__` ловит только уже записанные события и не гарантирует полную обработку всех событий, ожидающих в очереди watchdog/ОС. Если первая pre-source проверка `_change_alarm.is_alarmed()` после initial active-check вернула `True`, `TC.go()` обязан сразу поднять `TransactionalChangeError`, не полагаясь на cantok identity для already-true `ConditionToken`; этот pre-source check выигрывает даже при already-cancelled user/source token, а user-token priority действует только после создания composite token в exception-path. После создания `cond_stopped`/`cond_changed` и `merged = token + cond_stopped + cond_changed`, но до `source.go(...)`, `TC.go()` делает вторую явную pre-source проверку: если `_active is not True` → `TransactionStoppedDuringIterationError`, иначе если `_change_alarm.is_alarmed()` → `TransactionalChangeError`. Обе явные pre-source проверки намеренно не вызывают `merged.check()` и не учитывают user/source token; pre-cancelled user/source token начинает конкурировать только после входа в `source.go()`. Именно к этой второй pre-source проверке и final checks внутри `TC.go()` относится правило `stop > change`; в exception-path приоритет определяется `CancellationError.token`/`__cause__.token`, выбранным cantok на момент `_check_token`, без повторного чтения текущего `_active`. Clean `__exit__` после успешного `_stop()` проверяет только `_change_alarm`. Первая already-alarmed проверка до internal tokens идёт раньше этого правила; alarm, установленный после неё, обрабатывается следующими проверками по обычному приоритету. Это закрывает race между initial checks и созданием/первой проверкой composite token без опоры на cantok identity for already-true internal tokens; если alarm/stop случается после второй pre-source проверки, composite уже создан и действует invariant `test_composite_token_cancellation_exposes_subtoken_identity_in_exc_token`.

### Final-check и cleanup priority

- Только `TC.go()` и clean `__exit__` превращают alarm в `TransactionalChangeError`; прямой `_stop()` и `_stop_safely_at_exit()` не поднимают `TransactionalChangeError` только из-за установленного `_change_alarm`. Если исключение покидает тело `with` или callback в `apply`, оно не подменяется `TransactionalChangeError`; cleanup/_stop exception может замаскировать его только в явно описанных ниже случаях. Если пользователь поймал исключение внутри `with`, `__exit__` видит clean exit; alarm не очищается, поэтому после успешного `_stop()` clean `__exit__` всегда поднимает `TransactionalChangeError`, когда `self._change_alarm.is_alarmed()` true. Для non-TC `crawler.apply(transactional=True)` callback exception проходит через внутренний `with` и запускает `__exit__`; callback exception в активном `TC.apply` только выходит из `apply`, а cleanup остаётся ответственностью окружающего `with`. Отдельной обработки callback exceptions в `apply` не добавлять. При clean exit любой `Exception` из `_stop()` пробрасывается и имеет приоритет над final-check; `BaseException` из `_stop()` тоже не ловится, пробрасывается наружу, final-check не выполняется. Если body/callback уже падает, `__exit__` подавляет любой `Exception` из cleanup `_stop()` через `ResourceWarning`, включая `Exception` из `atexit.unregister()`/`observer.join()` после `_active=False`; при обычных warning-фильтрах исходное body/callback exception сохраняет приоритет, но если этот `ResourceWarning` превращён в exception, он может замаскировать исходное исключение. `BaseException` из `_stop()` не ловится и маскирует исходное body/callback exception. Если `_stop()` падает до `_active=False` (например, на `observer.stop()`), TC может остаться active/registered: `_active` остаётся True, atexit hook остаётся registered, watcher'ы observer'а могут быть активны; если после `_active=False` — остаётся terminal. При clean exit или прямом вызове `_stop()` исключения из `atexit.unregister()`, `observer.join()` и `observer.is_alive()` пробрасываются, если они возникли в рамках того же успешного `_stop()` после того, как он уже выставил `_active=False`; если unregister и join/is_alive падают, наружу выходит исключение из `finally` (`join()` или последующий `is_alive()`/warning). Если `atexit.unregister()` падает, а `observer.join()` не падает, но subsequent join-timeout `ResourceWarning` превращён warning-фильтрами в exception, наружу выходит warning-exception из `finally`, а unregister exception маскируется. Если `observer.join(timeout=5.0)` вернулся без исключения, но `observer.is_alive()` всё ещё true, эмитим `ResourceWarning`: join-timeout сам по себе не делает `_stop()` failed; если warning-фильтры не превратили его в exception и нет уже pending exception из `atexit.unregister()`/`observer.join()`/`observer.is_alive()`, `_stop()` возвращает нормально и clean `__exit__` продолжает final-check. Если `observer.is_alive()` сам бросает `Exception`, это обычное `Exception` из `_stop()` после `_active=False`, не timeout-warning. Если warnings-as-errors превращают `ResourceWarning` в exception, действует обычная семантика Python warning filters и это считается `Exception` из `_stop()`.

### Lifecycle и shutdown

- Окно наблюдения начинается после успешного `observer.start()` внутри `_start()`, до `self._active = True`, возврата `__enter__` и первого `go()`. Дополнительного readiness-wait после `observer.start()` не делаем; возврат `start()` — единственная граница, немедленные события остаются в best-effort модели ОС/watchdog. Handler не проверяет `_active`: доставленное в этом окне событие записывается в `ChangeAlarm` и потом обрабатывается обычным `go()`/clean `__exit__` final-check, если setup завершился успешно.
- TC — **строго one-shot lifecycle**: успешный путь `_active: None → True → False`; failed setup при `_active is None` переводит TC в `_active=False` terminal. Невалидные переходы `_start()` на active/stopped TC состояние `_active` не меняют и кидают `TransactionAlreadyActiveError` (попытка `_start()` на активном TC) или `TransactionInactiveError`. State-specific inactive messages есть у `_start()`/`_stop()`; `TC.transaction` использует свой единый inactive message для fresh/stopped TC, а `go`/`__iter__`/`apply` — отдельный общий iteration inactive message.
- `_start()`/`_stop()` приватные и не имеют stable public API; тесты могут вызывать их напрямую для проверки lifecycle-инвариантов.
- Транзакция остановлена во время активной итерации (главный поток вышел из `with` до завершения worker-потоков) → неисчерпанные итераторы, уже вошедшие в активный `TC.go()`, получают `TransactionStoppedDuringIterationError` через `cond_stopped` на ближайшем token-check или post-loop check этого же generator'а при следующем продвижении; если generator больше не продвигается/брошен, stop-error не материализуется. Уже выполняющийся `TC.apply()` эквивалентен уже вошедшему внутреннему `TC.go()` только после первого продвижения `self.go(token)` внутри apply-loop; stop/change exceptions из этого generator'а выходят из `apply()`. Если scope остановлен после initial `_active`-check в `TC.apply()`, но до первого продвижения inner `go`, то при валидном `function` результат — обычный `TransactionInactiveError` из first `next()` inner generator; при невалидном `function` сохраняется validation-order и выигрывает ошибка `PossibleCallMatcher`. Если `next()` уже прошёл последний `_check_token` перед `_stop()`, он может вернуть один path; ошибка обязана появиться на следующем продвижении или post-loop check при исчерпании этого `TC.go()`. Уже исчерпанный generator после выхода остаётся exhausted и продолжает давать `StopIteration`. Clean `__exit__` сам по себе не поднимает `TransactionStoppedDuringIterationError`. «Уже вошедшие» = generator был хотя бы раз продвинут, пока `_active is True`; generator, созданный в `with`, но впервые продвинутый после выхода, даёт `TransactionInactiveError`. Новые post-exit обращения к stopped scope проходят обычный inactive-check и дают `TransactionInactiveError`: `iter(scope)`/`scope.go()` — при первом продвижении generator'а (`next()`/`list(...)`), `scope.apply()` — сразу.
- Post-exit `scope.apply()` даёт `TransactionInactiveError` сразу только для валидных/default аргументов; `transactional=False` и non-bool `transactional` сохраняют validation-order `TC.apply` и дают `InvalidTransactionalArgumentError` даже на stopped TC.

### Дополнительные lifecycle-инварианты

Любой `ImportError` из import statements `watchdog.observers` и `watchdog.events` конвертируется в `WatchdogNotInstalledError`, включая сломанный watchdog/transitive import; raw import error наружу не пробрасывать. Отдельный test subcase для broken transitive import не нужен: missing-module сценарий закрепляет broad `except ImportError` branch.

`__iter__` уже определён в `AbstractCrawler` через `self.go()` → проверка активности произойдёт при первом `next()`.

**Lifecycle cached_property**: после успешного `_stop()` cached-объекты остаются в `self.__dict__` для final-check/cleanup; если cached-property `_observer` упала до `return observer`, `_observer` не закеширован и не является стабильной точкой инспекции, поэтому failure-path tests проверяют fake logs/side effects, а не читают `tc._observer`. Публичные методы при `_active=False` кидают `TransactionInactiveError`, кроме validation-order в `TC.apply`: `transactional=False` и non-bool `transactional` всегда дают `InvalidTransactionalArgumentError` раньше lifecycle-check.

### Observer setup / scheduling / cleanup failure paths

Non-directory base paths не подписываются через watchdog; если schedulable base (существующих directory base на момент `_start()`) нет, код всё равно вызывает `_load_watchdog()`, создаёт Observer и вызывает `start()` без `schedule` (не оптимизировать в no-op). В mixed-path crawler существующие directory bases всё равно schedule'ятся и защищены; guarantee отсутствует только для base entries, которые не были directory на `_start()`. File/missing base не покрывается как самостоятельный watched base; если такой path находится внутри scheduled watched base, событие обрабатывают только те scheduled handlers, которым watchdog реально доставил event, и каждый применяет фильтры своего leaf. Unscheduled file/missing leaf никогда не добавляет свои фильтры через scheduled parent другого leaf; если у того же leaf есть другой scheduled base, реально получивший событие, фильтры этого leaf применяются в descendant pipeline этого scheduled handler'а. File/missing base entry не создаёт отдельную per-entry подписку/handler и не вносит отдельные фильтры. Любая ошибка на setup/start path до `self._active = True` идёт по обычному failed `_start()` raw-error пути: при обычных warning-фильтрах пробросить исходное setup/start exception без дополнительного wrapping в `DirstreeError`, после best-effort cleanup и перевода `_active=False` terminal; это не отменяет конвертацию import/version ошибок внутри `_load_watchdog()` в `WatchdogNotInstalledError`. Ошибки этого пути внутри cached-property `_observer`: `unwrap_to_crawlers`, `Path(base).is_dir()`, handler init, `_compile_excludes()`, `Path(base).resolve(strict=False)`, `observer.schedule(...)` (например, из-за исчезнувшего base); ошибки в `_start()` после возврата `observer_ref = self._observer`: `observer_ref.start()` и `atexit.register(self._stop_safely_at_exit)`. Cleanup у ошибок внутри `_observer` выполняется локально в `_observer`; так как `_observer` впервые вызывается из `_start()` под `_lifecycle_lock`, этот setup-cleanup тоже происходит под lock и при обычных warning-фильтрах до `observer.start()` пытается вызвать `observer.stop()` затем `observer.join(timeout=5.0)`. Если `ResourceWarning` от `observer.stop()` cleanup превращён warning-фильтрами в exception, `observer.join()` в этом setup-cleanup не гарантируется. Правило «`observer.join()` outside `_lifecycle_lock`» относится к нормальному `_stop()` и failed `observer_ref.start()`/`atexit.register` cleanup в `_start()` после успешного возврата `_observer`. `Observer()` и `observer.daemon = True` стоят до schedule-cleanup `try`; если они падают, `_start()` делает `_active=False` terminal, но stop/join cleanup не запускается, потому что возвращённого `observer_ref` ещё нет. `observer.start()` остаётся в `_start()`, а не внутри cached-property `_observer`. Cleanup выполняется best-effort: только `Exception` из cleanup превращается в `ResourceWarning`; `BaseException` из cleanup не ловится. Если cleanup `ResourceWarning` превращён warning-фильтрами в exception, действует обычная Python warning semantics: warning-exception может замаскировать исходную setup/start ошибку; дополнительного подавления не добавлять. Такой failed setup даёт `_active=False` terminal; повторное использование этого TC запрещено. Если empty Observer `start()` разрешён и успешен, обход ведёт себя как обычный `source.go()` без watchdog-покрытия будущего появления этих base; при этом TC всё равно проходит обычный lifecycle, регистрирует atexit hook и вызывает `source.go(token + cond_stopped + cond_changed)`, просто `cond_changed` не сработает без доставленных событий.

Failed `atexit.register` моделируется как no-hook-registered: реализация не вызывает `atexit.unregister()` на этом пути, а тестовый fake, который падает, не должен сначала сохранять hook.

Setup/start cleanup `join(timeout=5.0)` ловит только исключения из `join()`; `is_alive()` после join не проверяет и timeout-`ResourceWarning` не эмитит. Timeout-warning rule есть только у обычного `_stop()`. Для failures внутри `_observer` cleanup `stop()` и `join()` ожидаются под `_lifecycle_lock`; outside-lock `join` проверяется только для normal `_stop()` и failures после успешного возврата `_observer` (`observer_ref.start()`/`atexit.register`). Для failed `observer_ref.start()`/`atexit.register` cleanup действует то же warning-as-error правило: если `ResourceWarning` из `observer_ref.stop()` cleanup стал exception, subsequent `join()` не гарантируется.

### Источники, зависимости и сообщения

- Отсутствие `watchdog` → `WatchdogNotInstalledError(DirstreeError, ImportError)` при вызове `_start()` (или внутри `apply(transactional=True)` после успешной callable/type validation аргументов). Сам по себе доступ к `crawler.transaction` watchdog не загружает. `_load_watchdog()` выполняется до разворачивания `source`, поэтому при unsupported source и отсутствующем watchdog ошибка зависимости выигрывает. Supported source iff `isinstance(source, CrawlersGroup)`, `isinstance(source, TransactionalCrawler)`, or `isinstance(source, Crawler)`; для `CrawlersGroup`/`TransactionalCrawler` правило рекурсивное: каждый unwrapped leaf должен быть `Crawler`, иначе `_start()` после успешных `_load_watchdog()`, `Observer()` и `observer.daemon = True` даёт raw `TypeError`. Пустой `CrawlersGroup` поддержан только через тот же zero-schedule `Observer.start()` path: unwrap даёт 0 leaf'ов, `_observer` создаёт 0 handlers/0 schedules, успешный empty `Observer.start()` ведёт к обычному пустому traversal; если empty `Observer.start()` падает, это failed setup raw-error path, no-op fallback не добавлять. Это observer-unwrapping type rule, не гарантия полезности inactive TC при traversal. Для direct source `TransactionalCrawler(existing_tc)` outer `_observer` unwrap'ит nested TC до source без lifecycle-check; traversal всё равно вызывает `existing_tc.go(...)`, поэтому active nested TC итерируется с дублированием observers, а fresh/stopped nested TC даёт `TransactionInactiveError` при первом продвижении traversal. Если inner и outer TC оба alarmed, outer не переписывает исключение из `existing_tc.go(...)`; кто даст сообщение, определяется фактическим местом cancellation (child exception или outer `cond_*`), priority не гарантируется. `PythonCrawler` принимается как наследник `Crawler`. Любой другой `AbstractCrawler` subclass после успешных `_load_watchdog()`, `Observer()` и `observer.daemon = True` даёт raw `TypeError`, intentionally не `DirstreeError`. Runtime rule: accept every `isinstance(source, Crawler)`; semantic compatibility is subclass author's responsibility and is not validated. Subclasses of `Crawler` поддержаны только если сохраняют его семантику: `.paths` задаёт watched bases, `_compile_excludes()`/`extensions`/`filter`/`only_files` соответствуют путям, которые выдаёт `go()`/`_traverse()`, а `cancellation_exception`/`raise_on_cancel` ведут себя как у обычного `Crawler`.
- При значимом изменении в `observed area` → `TransactionalChangeError` с сообщением `Directory changed during transactional iteration: <event_type> at <path>` (для `moved` — `... moved from <path> to <dest>`). Структурированных полей нет, вся диагностика в тексте (тесты ассертят через `match=match(...)`). Ordinary `created`/`modified` watched base игнорируются; доставленные watchdog'ом `deleted` с boundary endpoint и `moved` с boundary endpoint не проходят через yield-фильтры и считаются значимыми; parent-watch для усиления этой гарантии не добавляем.

### Классификация событий и фильтрация

В `on_any_event(event)` (вызывается фоновым потоком watchdog для каждого события). `event: watchdog.events.FileSystemEvent` здесь descriptive type only; production signature не должен ссылаться на watchdog-типы в annotations (использовать untyped/`Any` с локальными import ignores). У event есть атрибуты `event.event_type: str` (`'created'` / `'deleted'` / `'modified'` / `'moved'` — это именно те строки, которые попадут в `ChangeAlarm.message`), `event.src_path: str`, `event.is_directory: bool`, и для moved — `event.dest_path: str`. `event.is_synthetic`, если есть, не читаем: synthetic/non-synthetic события обрабатываются одинаково, если `event_type` поддержан. Логика:
1. Если `event.event_type` не входит в `{'created', 'deleted', 'modified', 'moved'}` → ignore.
2. Нормализация делается через helper уровня handler'а: `classify_endpoint(event_path) -> Endpoint(kind, message_path, filter_path)`, где `Endpoint` не публичная сущность и может быть локальной `NamedTuple`/dataclass или tuple с этими полями; `kind` — `inside_descendant`, `boundary` или `outside`. Если `_bases == ()`, helper без `Path.resolve()` возвращает `outside`: `message_path = event_path` (оригинальная watchdog-строка, не `Path`-нормализация), `filter_path = None`. Иначе helper сначала делает `resolved_event = Path(event_path).resolve(strict=False)` и только потом выбирает `kind`: ищет matching `resolved_base` из `_bases` с максимальным `len(resolved_base.parts)` через попытку `resolved_event.relative_to(resolved_base)`; при равной длине — первая пара в `_bases`. Исключения из `Path(...).resolve(strict=False)` не ловятся и не превращаются в `outside`; только failed `relative_to()`/`ValueError` при пробе конкретной base — штатный no-match, пробуем следующую base. Если ни одна base не подошла, это `outside` с `message_path = event_path`, `filter_path = None`. `relative_tail = resolved_event.relative_to(resolved_base)` использовать из успешного match, затем `path = crawler_base / relative_tail`. Этот longest-base-wins порядок intentional: если более широкий base тоже матчится, он не участвует в оценке значимости; например, для `Crawler(root, root / 'sub')` ordinary `created`/`modified` на `root/sub` классифицируется как boundary выбранного `root/sub`, игнорируется и не получает второй попытки как descendant `root`. Boundary iff `resolved_event == resolved_base`; после `relative_to()` это соответствует пустому `relative_tail.parts`, тогда `message_path = filter_path = crawler_base`. Для inside descendant оба path равны crawler-coordinate path (`crawler_base / relative_tail`). Фильтры всегда получают `endpoint.filter_path`, а `ChangeAlarm.set_first(...)` — `endpoint.message_path`. Path, равный выбранному base, считается boundary этого base, а не descendant более широкого base. Для ordinary non-moved event (здесь ordinary = `event_type != 'moved'`) сделать `endpoint = classify_endpoint(event.src_path)`; `outside` → ignore. Для `moved` helper вызывается на `event.src_path` и `event.dest_path`.
3. Boundary события watched base не проходят через `exclude`/`extensions`/`filter`/`only_files`: ordinary `deleted` на самом watched base trip'ит; ordinary `created`/`modified` на watched base игнорируются как шум/невозможный start-state event, включая `only_files=False`. Boundary significance применяется после успешной normalization/classification; ошибки normalization не подавляются и не превращаются в boundary trip. Для `moved` успешная classification означает, что оба endpoint'а классифицированы; `outside` — успешная classification. Если любой endpoint raises во время normalization/classification, exception не ловится, handler прерывается и `set_first` не вызывается, даже если другой endpoint был boundary.
4. Для non-boundary directory events: при snapshot `only_files=True` → ignore (как `_traverse`, который при `only_files=True` не yield'ит директории). Игнорируется только сам delivered event с `event.is_directory is True`; handler не сканирует содержимое moved/deleted directory и не подавляет отдельно доставленные watchdog descendant file events — такие file events идут через обычный pipeline и могут trip'ить. Для любого non-boundary `moved` с `event.is_directory is True` и snapshot `only_files=True` игнорировать всё событие целиком до endpoint-фильтров. При `only_files=False` descendant directory `created`/`deleted`/`modified` и inside endpoints `moved` проходят обычные `exclude`/`filter`; descendant directory `modified` не игнорировать как parent-noise.
5. Проверка exclude использует `path = endpoint.filter_path` (для outside endpoint pipeline не запускается): `excluded = excludes_spec.match_file(path) or (is_directory and excludes_spec.match_file(f'{path}/'))`; если `excluded` → ignore. Первый вызов использует `Path` в той же relative/absolute форме, что текущий `_traverse`; для symlink descendants lexical parity не обещается. `is_directory` в handler всегда берётся из `event.is_directory`, а `Path.is_dir()`/`Path.is_file()` в event pipeline не вызывать. Trailing-slash check для dir-event соответствует условию `_traverse`: `child_path.is_dir() and excludes_spec.match_file(f'{child_path}/')`. Родительские директории при exclude не проверяем — для строгой behavioral parity с `_traverse`.
6. Для snapshot `extensions`: `if not is_directory and _extensions is not None and endpoint.filter_path.suffix not in _extensions: ignore`. Directory events intentionally не проходят extension-check; для поддерживаемых `Crawler`/`PythonCrawler` `extensions is not None` несовместим с `only_files=False`, и `Crawler`-subclasses должны сохранять этот invariant.
7. Если snapshot `filter is not None` — вызвать `filter(endpoint.filter_path)` и трактовать результат по truthiness, ровно как `_traverse`: truthy пропускает; любой falsey результат (`False`, `None`, `0`, пустая коллекция и т.п.) отфильтровывает. В `try/except Exception` оборачиваются ровно вызов `filter(...)` и вычисление truthiness его результата; такие исключения считаются user-filter exception. Для `deleted`/moved-out path объект может уже отсутствовать; если stateful filter вернул falsey, событие игнорируется. Если user `filter` кинул `Exception`, это сразу считается trip'ом, затем exception подавляется; raw exception/traceback в пользовательское сообщение не включать. Message при filter exception остаётся обычным доменным message текущего события: для non-moved по `endpoint.message_path` и `event.event_type`; для `moved` — см. шаг 8, с обоими endpoint'ами. Вне блока user `filter` в `on_any_event` не ловить вообще ничего: любые `Exception`/`BaseException` из normalization/exclude/extensions не пишут `ChangeAlarm`, не меняют `_active`, не превращаются в доменный trip и не пробрасываются через `TC.go()`/`__exit__`; они уходят наружу в watchdog dispatcher thread, дальше это поведение watchdog.
8. Для `moved` — сначала нормализовать/classify оба endpoint'а и вычислить `src_for_message = src_endpoint.message_path` / `dest_for_message = dest_endpoint.message_path`, затем применять весь yield-filter pipeline (`exclude`, `extensions`, custom `filter`; `only_files` обработан выше) к inside endpoints в порядке src → dest. Здесь `inside endpoint` = endpoint с `kind == inside_descendant`; boundary endpoint никогда не проходит yield-filter pipeline. Для non-boundary `moved` значимость = OR по inside endpoint'ам с short-circuit в порядке src → dest; outside endpoint сам по себе событие значимым не делает. Если хотя бы один endpoint — boundary watched base, событие сразу значимо: yield-фильтры и `only_files` к обоим endpoint'ам не применяются, но message всё равно содержит оба endpoint'а. Для non-boundary `moved` directory при `only_files=True` шаг 4 уже дал ignore, поэтому endpoint pipeline ниже не запускается. Для каждого inside endpoint используются проверки из шагов 5–7 с `endpoint.filter_path` и `is_directory=event.is_directory`, включая trailing-slash exclude для directory events и пропуск extension-check для directory events. Состояния endpoint'а: inside descendant → crawler-координаты в message и участие в фильтрах; сам watched base → boundary endpoint: `crawler_base` в message, без фильтрации, сам по себе trip'ит даже при `only_files=True`; outside → raw `event.src_path`/`event.dest_path` от watchdog в message без фильтрации. Это действует и для boundary move: boundary endpoint пишется как `crawler_base`, outside endpoint — как raw watchdog string. Для mixed move message intentionally mixes formats: inside/boundary endpoint — crawler-coordinate `Path`, outside endpoint — raw watchdog string. Boundary move определяется только классификацией endpoint'а (`resolved_event == resolved_base`), не lexical/string equality event path with `crawler_base`; `outside -> inside descendant` trip'ит только если inside dest проходит фильтры; `inside descendant -> outside` trip'ит только если inside src проходит фильтры; `outside -> outside` без boundary/inside endpoint'ов игнорируется. В одном multi-path `Crawler(a, b)` src и dest могут нормализоваться через разные watched base одного handler'а; для `Crawler(a) + Crawler(b)` это будут два leaf-handler'а с first-delivered message без source-order guarantee. Inside endpoints фильтруются в порядке src → dest: если первый inside endpoint прошёл фильтры, второй endpoint не проверяется; если первый отфильтрован, проверяется второй. Filter exception на любом checked endpoint сразу записывает moved-message с обоими endpoint'ами; оставшиеся endpoints этого события не проверяются. `set_first(src_for_message, 'moved', dest_for_message)` всегда пишет оба endpoint'а.
9. Если фильтры пройдены — вызвать `set_first`: для обычных событий `self._change_alarm.set_first(endpoint.message_path, event.event_type)`, для `moved` — `self._change_alarm.set_first(src_for_message, 'moved', dest_for_message)`. Bound method `set_first` не кешировать в handler; lookup делается во время обработки события, чтобы instance-level spy в тестах видел вызовы. Под Lock'ом внутри — если уже сработало, новое игнорируется (first-wins). Handler не делает early-return по `change_alarm.is_alarmed()`: после первого alarm он продолжает обычную обработку событий и при проходе фильтров снова вызывает `set_first`; first-wins остаётся ответственностью `ChangeAlarm`.

### Взаимодействие с существующими опциями

- **`freeze=True`** (существующий параметр `Crawler` — см. `crawler.py`, заранее собирает snapshot всех путей через `list(_traverse(...))` перед итерацией): Observer стартует ДО первого `go()`. При `freeze=True` источник сначала строит snapshot, потом отдаёт пути — оба прохода защищены условным токеном для доставленных watchdog-событий. Race `Path.rglob` с удалением может всё ещё дать bare `FileNotFoundError`, см. «Известные ограничения», п.2.
- **`raise_on_cancel`**: TC различает три источника отмены через identity внутренних condition-токенов (`cond_stopped`, `cond_changed`):
  - Срабатывание `cond_changed` (изменение в директории) → `TransactionalChangeError`, если именно `cond_changed` пришёл в `CancellationError.token` / `__cause__.token`, если change найден post-loop check внутри `TC.go()` или clean `__exit__` final-check.
  - Срабатывание `cond_stopped` (транзакция остановлена во время итерации) → `TransactionStoppedDuringIterationError`, если именно `cond_stopped` пришёл в `CancellationError.token` / `__cause__.token` или если stop выбран post-loop check. Оба internal outcomes не отменяют приоритет explicit user-token при одновременной отмене в exception-path.
  - Любая другая отмена (от user-токена, переданного в `go(token)`, или от `source.token`) — пробрасывается как есть, в том типе, который её поднял (`CancellationError` или кастомное исключение из `raise_on_cancel=...`), если именно этот внешний токен стал `CancellationError.token` (или `exc.__cause__.token`). При `raise_on_cancel=False` внешняя отмена без internal stop/change остаётся silent.
  - Принадлежность исключения конкретному токену проверяем через `is`-сравнение `getattr(exception, 'token', None) is cond_stopped` / `... is cond_changed`. Если `source.raise_on_cancel` — кастомное исключение, `Crawler._check_token` оборачивает оригинальный `CancellationError` через `raise X from original`, и мы достаём его через `exc.__cause__` только когда outer exception соответствует configured custom `raise_on_cancel` у source/leaf; эту wrapper-проверку делать раньше прямого `isinstance(exc, CancellationError)`, потому что configured custom exception может само наследоваться от `CancellationError`. Если exception совпал с configured custom wrapper, но `exc.__cause__` не является `CancellationError`, это пользовательское исключение: пробросить как есть и не применять direct `isinstance(exc, CancellationError)` branch. Matching source/leaf: unwrap как для `_observer` до leaf `Crawler`; учитывать только `leaf.cancellation_exception is not None`; class-form матчится через `type(exc) is configured_class` (не `isinstance`), instance-form — через `exc is configured_instance`; `raise_on_cancel=True` без custom exception не считается wrapper'ом. Для instance-form stale `__cause__` на повторно поднятом configured exception instance доверяется так же, как текущий cause; это documented limitation, а тесты/пользовательские сценарии, которым важна корректная disambiguation, должны использовать fresh exception instance. Это не общий unwrap любого пользовательского исключения с `CancellationError` в `__cause__`: traversal-side `filter` exceptions с собственным unrelated `__cause__` пробрасываются как пользовательские. Direct `CancellationError`/subclass, брошенный пользовательским кодом (например, traversal-side `filter`), конвертируется только при exact internal `token is cond_stopped/cond_changed`; с другим token или без token пробрасывается как есть. При одновременной отмене explicit user-token из `scope.go(token)` и внутренних stop/change в exception-path (`_check_token` поднял `CancellationError` или custom `raise_on_cancel`) выигрывает explicit user-token, потому что уже созданный cantok composite token проверяет leaf-токены слева направо в порядке `token + cond_stopped + cond_changed`, а первый cancelled leaf становится `CancellationError.token`. Если одновременно cancelled только `cond_stopped` и `cond_changed`, в exception-path и post-loop path выигрывает `cond_stopped`; `TransactionalChangeError` поднимается только если stop не выбран. Если `_check_token` уже выбрал `cond_changed`, а stop случился до `except`, exception-path всё равно даёт `TransactionalChangeError`; приоритет не пересчитывается по текущему `_active`. Identity конкретного leaf гарантируется для composite, созданного до cancel этого leaf-токена; для pre-cancelled external token identity не обещаем, но он всё равно не конвертируется в internal stop/change, потому что token не `cond_stopped`/`cond_changed`. При silent cancellation (`source.go()` завершился нормально из-за `raise_on_cancel=False`) exception-token нет, external-token priority не восстанавливается вручную: если post-loop видит stop/change, он поднимает соответствующую internal ошибку даже при cancelled user/source token; stop имеет приоритет над change. `source.token` добавляется внутри `source.go()`, поэтому при его одновременном срабатывании со stop/change отдельный приоритет не обещаем и специальную priority-логику не добавляем: source-token пробрасывается только когда он реально пришёл в `CancellationError.token`/`__cause__.token`.
- `raise_on_cancel=cantok.CancellationError` как class-form rejected at construction существующей `Crawler`-валидацией, поэтому runtime disambiguation для exact configured `CancellationError` class не нужен.
- **`CrawlersGroup`**: `group.transaction` возвращает `TransactionalCrawler(group)`. `_observer` разворачивает группу до листовых Crawler-ов, создаёт handler на каждый лист, привязывает их к одному Observer и одному shared `ChangeAlarm`. Реализация вызывает `observer.schedule(...)` для каждого leaf/base; возможная внутренняя дедупликация одинаковых OS-подписок watchdog'ом не является нашим контрактом.
- **Вложенный active TC внутри group** (`with Crawler('a').transaction as tc_a: mixed = tc_a + Crawler('b')`): внутренний `unwrap_to_crawlers()` транзитивно разворачивает TC до underlying Crawler. Это означает дублирование наблюдения (Observer активного TC + Observer outer TC). Любое исключение, поднятое child `tc_a.go(...)` при outer group traversal, outer `TransactionalCrawler.go()` не преобразует, кроме своих собственных `cond_stopped`/`cond_changed`; приоритет outer `ChangeAlarm`/message не гарантируется. Inactive TC как child группы не является поддерживаемым пользовательским сценарием: outer `_start()` намеренно разворачивает inactive TC для observer setup и не отвергает его по lifecycle state, но `CrawlersGroup.go()` позже вызовет `go()` на inactive child и получит его `TransactionInactiveError` при первом продвижении traversal. Типичное использование — `(c1 + c2).transaction`.
- **Несколько `paths` в одном Crawler**: один Observer, несколько `schedule()` с `recursive=True`.
- **Внешний `token`**: передаваемый снаружи в `scope.go(token)`, `scope.apply(fn, token=...)` или `crawler.apply(fn, token=user_token, transactional=True)` композируется с нашим `ConditionToken` через `+` и продолжает работать (в `crawler.apply(..., transactional=True)` token передаётся внутрь `scope.go(token)`, так что итерацию можно отменить извне).

## Известные ограничения (документируем явно)

1. **Платформенная задержка доставки событий**. OS notification mechanisms имеют inherent latency между мутацией и доставкой handler'у. Если изменение случилось *сразу перед* `_check_token` — отдадим ещё один path до доставки события; следующий yield/check сорвётся, если он будет. На последнем элементе изменение ловится только если событие уже записано в `ChangeAlarm` до final-check в `go()`/`__exit__`; drain очереди watchdog/ОС не добавляем (`drain` = явное ожидание/flush очереди watchdog/OS events перед final-check). Best-effort гарантия.
2. `pathlib.Path.rglob('*')` делает stat per-entry → возможен `FileNotFoundError` из source. Это существующая проблема всего `Crawler`, подробно описанная в `issue_self.md`; позитивные тесты на устойчивость к этому сценарию не пишем до отдельного fix'а.
3. **`__cause__`-цепочка раскручивается на один уровень только для configured custom `raise_on_cancel`.** В `go()`-except проверяем, что outer exception соответствует source/leaf custom `raise_on_cancel` по helper-контракту из пункта **`raise_on_cancel`** в разделе «Взаимодействие с существующими опциями»; если да, но `exc.__cause__` не `CancellationError`, это raw user exception. Если cause — `CancellationError`, смотрим `exc.__cause__.token`. Многоуровневая обёртка (>1) текущей проверкой не распознаётся — пользователь получит raw outer exception вместо `TransactionalChangeError`. Для instance-form configured exception stale `__cause__` на повторно поднятом instance доверяется; это причина, по которой тесты instance-form создают fresh configured exception instance на каждый subcase. На практике `Crawler._check_token` делает один уровень `raise X from Y`, так что типово это не возникает.
4. Watchdog подписывается только на paths, которые являются директориями на момент `_start()`. Даже если schedulable bases нет, no-op optimization не делаем: после успешного `_load_watchdog()` и успешного empty `Observer.start()` file/missing base paths обходятся так же, как обычным `source.go()`, но их будущее появление не покрывается транзакционной гарантией как самостоятельный watched base; если empty `Observer.start()` падает, это обычный failed `_start()` raw-error path (исходное исключение без wrapping в `DirstreeError` после best-effort cleanup и `_active=False` terminal), no-op fallback не добавлять. Если file/missing path находится внутри scheduled watched base, каждый scheduled handler, которому watchdog доставил событие, обрабатывает его своим descendant pipeline, но без boundary-статуса для file/missing base entry. Unscheduled file/missing leaf не вносит свои фильтры через scheduled parent другого leaf; работают только фильтры leaf handler'а, который реально получил event. Per-leaf handler всё равно создаётся один раз даже при `scheduled_bases == ()`.
5. `deleted` с boundary endpoint и `moved` с boundary endpoint trip'ят, если watchdog доставил такое событие нашему handler'у. Boundary определяется через `resolved_event == resolved_base`, не lexical/string equality с `crawler_base`. Parent-watch не добавляем, поэтому это не усиливает OS/watchdog delivery guarantees.
6. Строгий filter-parity предполагает path-oriented filters. Для `deleted`/moved-out events объект может уже отсутствовать к моменту вызова user `filter`; если stateful filter вернул falsey, событие считается отфильтрованным, если кинул `Exception` — transaction trip. Один и тот же filter callable может вызываться параллельно из нескольких пользовательских worker-потоков, одновременно итерирующих `scope`, и watchdog thread; синхронизация stateful filter'ов на пользователе.
7. Мутация event-handler snapshot fields (`_bases`, derived from `scheduled_bases` entries of `source.paths` that are directories at `_start()`, `extensions`, `exclude`, `filter`, `only_files`) во время активной транзакции не поддерживается, не детектируется и не валидируется: для handler'а `source.paths` снапшотится только как constructor-local `scheduled_bases` на `_start()`, а persistent handler field — `_bases`; traversal не снапшотится TC и остаётся live-поведением существующего `source.go()`; результат без гарантий. Для `CrawlersGroup.crawlers` то же правило: observer snapshot leaf'ов берётся на `_start()`, traversal остаётся live `group.go()` behavior. Для `filter` snapshot — это сохранение ссылки на callable, не заморозка его внутреннего mutable state. Остальные поля source crawler (`token`, `raise_on_cancel`, `frozen` (constructor option `freeze`) и т.п.) не снапшотятся TC отдельно и остаются live-поведением существующего `source.go()`.
8. Смена process `cwd` во время активной транзакции не поддерживается. Для relative base paths handler фиксирует resolved bases на `_start()`, а traversal остаётся поведением существующего `source.go()`.

## Структура файлов

**Новые:**
- `dirstree/crawlers/transactional/__init__.py` — пусто, без re-exports.
- `dirstree/crawlers/transactional/alarm.py` — класс `ChangeAlarm`; без watchdog-зависимостей.
- `dirstree/crawlers/transactional/crawler.py` — `TransactionalCrawler`, `_load_watchdog` и `FilteredEventHandler`; единственный production-модуль с `import watchdog`, без отдельного `handler.py`.
- `docs/plans/transactions.md` — byte-for-byte копия `docs/assets/plans/1 - transactional crawling.md`. Создать директорию `docs/plans/`, если её нет; синхронизировать при изменении исходного plan-файла.
- `issue_self.md` (корень проекта) — содержимое описано в приложении `issue_self.md` ниже.
- `tests/units/__init__.py`, `tests/units/crawlers/__init__.py`, `tests/units/crawlers/transactional/__init__.py` — пустые `__init__.py` для зеркальной структуры под `tests/units/`; правила зеркалирования в «Тестовая инфраструктура и конвенции».
- `tests/units/crawlers/transactional/test_crawler.py` — основной сьют для transactional; исключения из зеркалирования и cross-module coverage описаны в «Тестовая инфраструктура и конвенции», stress policy — в «Stress-сьют».
- `tests/python/__init__.py`, `tests/python/interpreter/__init__.py`, `tests/python/dependencies/__init__.py` и файлы invariant-тестов: `interpreter/test_atomicity.py`, `interpreter/test_atexit.py`, `dependencies/test_cantok.py`, `dependencies/test_watchdog.py` (детали ниже в «Python/dependency invariant tests»).

**Перемещение существующих тестов:**
- `tests/test_crawler.py` → `tests/units/crawlers/test_crawler.py`
- `tests/test_python_crawler.py` → `tests/units/crawlers/test_python_crawler.py`
- `tests/test_errors.py` → `tests/units/test_errors.py`
- `tests/conftest.py` — оставить в `tests/`; добавить shared fixtures/helpers, описанные в «Тестовая инфраструктура и конвенции».
- `tests/test_files/` (фикстурные директории) — оставить как есть.

**Изменяемые:**
- `dirstree/errors.py` — добавить шесть новых минимальных классов без custom `__init__`, не удаляя существующие errors; иерархия и смысл описаны в секции «Исключения».
- `dirstree/crawlers/abstract.py` — `transaction` через обычный `@property` (каждый доступ возвращает свежий `TransactionalCrawler`); расширить `apply` параметром `transactional: bool = False`.
- `dirstree/crawlers/crawler.py` — вынести `pathspec.PathSpec.from_lines('gitwildmatch', self.exclude)` в helper на классе `Crawler`:
  ```python
  def _compile_excludes(self) -> pathspec.PathSpec:
      return pathspec.PathSpec.from_lines('gitwildmatch', self.exclude)
  ```
  Использование: `spec.match_file(path) -> bool` в той же форме, что текущий `_traverse`; для directory trailing-slash проверки остаётся строка `f'{path}/'`. В `_traverse` заменить inline-компиляцию на `self._compile_excludes()`; `FilteredEventHandler` должен вызывать тот же helper и передавать path в той же relative/absolute форме, что `_traverse`, чтобы существующее exclude-поведение не менялось. Для symlink descendants эта parity не означает lexical path/exclude parity через symlink: действует правило `crawler-coordinate path` из терминов выше.
- `dirstree/crawlers/group.py` — **без изменений**: `transaction` и `apply(transactional=True)` наследуются из `AbstractCrawler`.
- `dirstree/__init__.py` — добавить публичный re-export **пяти** новых исключений, сохранив существующие re-export'ы вроде `IncompatibleCrawlerOptionsError`: `TransactionalChangeError`, `TransactionInactiveError`, `TransactionAlreadyActiveError`, `TransactionStoppedDuringIterationError` (user-facing: упоминается в README как сигнал для worker-потоков), `WatchdogNotInstalledError`. Шестое — `InvalidTransactionalArgumentError` — **внутренний** детектор ошибки использования API: internal здесь означает «не re-export из `dirstree` и не user-facing runtime-сценарий», но публичные методы могут его поднять при non-bool `transactional` и при `transactional=False` в `TC.apply()`; доступно через прямой импорт из `dirstree.errors`. `dirstree/crawlers/__init__.py` остаётся без transactional re-exports. `TransactionalCrawler` НЕ экспортируется (внутренний класс): ни из `dirstree`, ни из `dirstree.crawlers`, ни из `dirstree.crawlers.transactional`; тесты и internal code импортируют его напрямую из `dirstree.crawlers.transactional.crawler`.
- `pyproject.toml` — optional extra, pytest markers и watchdog typing/lint policy; детали в «Инфраструктура / CI».
- `.github/workflows/tests_and_coverage.yml` — compatibility и transactional coverage jobs; детали в «Инфраструктура / CI».
- `.github/workflows/lint.yml` — lint install policy без transactional extra; детали в «Инфраструктура / CI».
- `requirements_dev.txt` — test-only dev dependencies; детали в «Инфраструктура / CI».
- `README.md` — обновить раздел «Transactionality»; детали в «Документация».
- `CLAUDE.md` — создать/обновить test conventions; детали в «Тестовая инфраструктура и конвенции».

## Реализация ключевых элементов

**Код-скелеты**: импорты/аннотации/lint-прагмы частичные; при реализации довести до `mypy --strict`, а для `DefaultToken()` в default arguments сохранить repo pattern `# noqa: B008`.

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

Контракт исключений:

- `TransactionalChangeError` НЕ наследник `cantok.CancellationError`: иначе пользовательский `except CancellationError` мог бы silently ignore изменение. Перевод `CancellationError → TransactionalChangeError` делает сам `TransactionalCrawler.go`.
- `WatchdogNotInstalledError` наследует `ImportError`, чтобы его ловил `except ImportError`.

### `AbstractCrawler.transaction` и `apply`

```python
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from dirstree.crawlers.transactional.crawler import TransactionalCrawler


class AbstractCrawler:
    @property
    def transaction(self) -> 'TransactionalCrawler':
        from dirstree.crawlers.transactional.crawler import TransactionalCrawler
        return TransactionalCrawler(self)

    def apply(self, function, token=DefaultToken(), transactional=False):
        PossibleCallMatcher('.').match(function, raise_exception=True)
        if not isinstance(transactional, bool):
            raise InvalidTransactionalArgumentError('The `transactional` argument must be a bool.')
        if transactional:
            with self.transaction as scope:
                for path in scope.go(token):
                    function(path)
        else:
            for path in self.go(token):
                function(path)
```

Для plain `AbstractCrawler.apply` порядок ошибок из скелета считается контрактом: сначала валидируется callable, затем тип `transactional`.

### `TransactionalCrawler`

**Ключевые элементы дизайна**: three-state `_active`, `_lifecycle_lock` вокруг start/stop transitions, `_load_watchdog` как единственная точка import'а watchdog, per-TC cached `_observer`/`_change_alarm`, и `TC.apply(transactional=True)` с запретом `transactional=False`/non-bool.

#### Скелет класса и порядок проверок

```python
import atexit
import sys
import threading
import warnings
from functools import cached_property, lru_cache
from typing import Optional
from printo import describe_data_object, not_none

from dirstree.crawlers.transactional.alarm import ChangeAlarm

class TransactionalCrawler(AbstractCrawler):
    def __init__(self, source: AbstractCrawler, active: Optional[bool] = None) -> None:
        # Reject active=True/False so construction cannot fake lifecycle state; explicit None is equivalent to omitted.
        if active is not None:
            raise RuntimeError(
                'The `active` argument is reserved for `__repr__` only. '
                'TransactionalCrawler instances are normally created via `source.transaction`; '
                'direct construction without `active` is internal/test-only.'
            )
        self.source = source
        self._active = active
        self._lifecycle_lock = threading.Lock()

    def __repr__(self) -> str:
        return describe_data_object(
            self.__class__.__name__,
            (self.source,),
            {'active': self._active},
            filters={'active': not_none},
        )

    def __enter__(self) -> 'TransactionalCrawler':
        self._start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            self._stop()
        except Exception as stop_exc:
            if exc_type is not None:
                warnings.warn(
                    f'TransactionalCrawler cleanup failed after body exception: {stop_exc!r}',
                    ResourceWarning,
                    stacklevel=2,
                )
                return None
            raise
        # Final check catches already delivered events; it does not drain watchdog/OS queues.
        if exc_type is None and self._change_alarm.is_alarmed():
            raise TransactionalChangeError(self._change_alarm.get_message())

    @staticmethod
    @lru_cache(maxsize=None)
    def _load_watchdog():
        message = (
            'watchdog is not installed. Transactional mode requires Python >=3.9 '
            'and the transactional extra: pip install dirstree[transactional]'
        )
        if sys.version_info < (3, 9):
            raise WatchdogNotInstalledError(message)
        try:
            from watchdog.observers import Observer
            from watchdog.events import FileSystemEventHandler
        except ImportError as exc:
            raise WatchdogNotInstalledError(message) from exc

        class FilteredEventHandler(FileSystemEventHandler):  # type: ignore[misc]
            def __init__(self, source, change_alarm, scheduled_bases):
                super().__init__()
                self._change_alarm = change_alarm
                self._excludes_spec = source._compile_excludes()
                self._bases = tuple((Path(base), Path(base).resolve(strict=False)) for base in scheduled_bases)
                self._extensions = None if source.extensions is None else tuple(source.extensions)
                self._filter = source.filter
                self._only_files = source.only_files
            def on_any_event(self, event):
                ...

        return Observer, FilteredEventHandler

    @property
    def transaction(self) -> 'TransactionalCrawler':
        # Active TC.transaction returns self for property chaining; nested `with scope.transaction:` would call _start() again.
        if self._active is not True:
            raise TransactionInactiveError(
                'Cannot start a new transaction from an inactive TransactionalCrawler. '
                'TransactionalCrawler objects are single-use; create a new one via original_crawler.transaction.'
            )
        return self

    @cached_property
    def _change_alarm(self) -> 'ChangeAlarm':
        """Single ChangeAlarm per TC; first access is in _start() under _lifecycle_lock because cached_property is not thread-safe on 3.12+."""
        return ChangeAlarm()

    @cached_property
    def _observer(self):
        """Creates Observer and per-leaf handlers."""

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
                raise TypeError(
                    f'Unsupported source type {type(source).__name__!r} for TransactionalCrawler: '
                    f'expected Crawler (including PythonCrawler), CrawlersGroup, or TransactionalCrawler.'
                )

        Observer, FilteredEventHandler = self._load_watchdog()
        observer = Observer()
        observer.daemon = True                              # only daemon fallback; do not touch watchdog internals
        try:
            for leaf in unwrap_to_crawlers(self.source):
                scheduled_bases = tuple(base for base in leaf.paths if Path(base).is_dir())
                handler = FilteredEventHandler(leaf, self._change_alarm, scheduled_bases)
                for base in scheduled_bases:
                    observer.schedule(handler, str(base), recursive=True)
        except BaseException:
            # Best-effort cleanup even before observer.start(); cleanup Exception
            # failures become warnings; cleanup BaseException is not caught.
            try:
                observer.stop()
            except Exception as cleanup_exc:
                warnings.warn(
                    f'TransactionalCrawler observer cleanup failed during setup: {cleanup_exc!r}',
                    ResourceWarning,
                    stacklevel=2,
                )
            try:
                observer.join(timeout=5.0)
            except Exception as cleanup_exc:
                warnings.warn(
                    f'TransactionalCrawler observer cleanup join failed during setup: {cleanup_exc!r}',
                    ResourceWarning,
                    stacklevel=2,
                )
            raise
        return observer

    def _start(self) -> None:
        observer_ref = None
        setup_exc = None
        setup_tb = None
        with self._lifecycle_lock:
            if self._active is True:
                raise TransactionAlreadyActiveError('TransactionalCrawler is already active.')
            if self._active is False:
                raise TransactionInactiveError(
                    'TransactionalCrawler has already been stopped and cannot be restarted. '
                    'Create a new one via original_crawler.transaction.'
                )
            try:
                # Force both cached_properties under lock, even when no base path is schedulable.
                _ = self._change_alarm
                observer_ref = self._observer
                # cached_property creates Observer/setup; start/register happen under the same lock.
                observer_ref.start()
                # If register fails, treat it as setup failure: stop/join observer and make TC terminal.
                atexit.register(self._stop_safely_at_exit)
                self._active = True
            except BaseException as exc:
                # Raw watchdog/OS setup error: cleanup best-effort and make this TC terminal.
                setup_exc = exc
                setup_tb = exc.__traceback__
                self._active = False
                if observer_ref is not None:
                    try:
                        observer_ref.stop()
                    except Exception as cleanup_exc:
                        warnings.warn(
                            f'TransactionalCrawler cleanup failed after start error: {cleanup_exc!r}',
                            ResourceWarning,
                            stacklevel=2,
                        )
        if setup_exc is not None:
            if observer_ref is not None:
                try:
                    observer_ref.join(timeout=5.0)
                except Exception as cleanup_exc:
                    warnings.warn(
                        f'TransactionalCrawler cleanup join failed after start error: {cleanup_exc!r}',
                        ResourceWarning,
                        stacklevel=2,
                    )
            raise setup_exc.with_traceback(setup_tb)

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
        # join() still runs if unregister raises; _active is already False/terminal.
        try:
            atexit.unregister(self._stop_safely_at_exit)
        finally:
            # join() outside lock — avoids deadlock if observer hangs; 5s timeout protects against FSEvents stalls on macOS.
            observer_ref.join(timeout=5.0)
            if observer_ref.is_alive():
                warnings.warn(
                    'TransactionalCrawler observer thread did not terminate within 5s of stop(). '
                    'This is a watchdog/OS-layer bug; the thread is being left as a daemon.',
                    ResourceWarning,
                    stacklevel=2,
                )

    def _iter_leaf_crawlers_for_cancellation_matching(self):
        from dirstree.crawlers.crawler import Crawler
        from dirstree.crawlers.group import CrawlersGroup

        # Same leaf-unwrapping rule as `_observer`, without loading watchdog;
        # unsupported sources do not match.
        def unwrap(source):
            if isinstance(source, TransactionalCrawler):
                yield from unwrap(source.source)
            elif isinstance(source, CrawlersGroup):
                for child in source.crawlers:
                    yield from unwrap(child)
            elif isinstance(source, Crawler):
                yield source
        yield from unwrap(self.source)

    def _is_source_raise_on_cancel_exception(self, exc: BaseException) -> bool:
        for leaf in self._iter_leaf_crawlers_for_cancellation_matching():
            configured = leaf.cancellation_exception
            if configured is None:                       # raise_on_cancel=True, no custom wrapper
                continue
            if isinstance(configured, type):
                if type(exc) is configured:              # exact type, not isinstance
                    return True
            elif exc is configured:                      # exception-instance form
                return True
        return False

    def go(self, token=DefaultToken()):
        if self._active is not True:
            raise TransactionInactiveError(
                'TransactionalCrawler can only be used inside an active transaction. '
                'Use `with crawler.transaction as scope:` and work with `scope` inside the block.'
            )
        if self._change_alarm.is_alarmed():
            raise TransactionalChangeError(self._change_alarm.get_message())
        cond_stopped = ConditionToken(lambda: self._active is not True)
        cond_changed = ConditionToken(lambda: self._change_alarm.is_alarmed())
        merged = token + cond_stopped + cond_changed
        if self._active is not True:
            raise TransactionStoppedDuringIterationError(
                'Transaction was stopped while iteration was in progress.'
            )
        if self._change_alarm.is_alarmed():
            raise TransactionalChangeError(self._change_alarm.get_message())
        try:
            for path in self.source.go(merged):
                yield path
        except BaseException as exc:
            # Check configured custom raise_on_cancel wrappers before direct
            # CancellationError: a custom wrapper may itself subclass CancellationError.
            if self._is_source_raise_on_cancel_exception(exc):
                if isinstance(exc.__cause__, CancellationError):
                    cancellation = exc.__cause__
                else:
                    raise
            elif isinstance(exc, CancellationError):
                cancellation = exc
            else:
                cancellation = None
            if cancellation is None:
                raise
            cancelled_token = getattr(cancellation, 'token', None)
            if cancelled_token is cond_stopped:
                raise TransactionStoppedDuringIterationError(
                    'Transaction was stopped while iteration was in progress.'
                ) from None
            if cancelled_token is cond_changed:
                raise TransactionalChangeError(self._change_alarm.get_message()) from None
            raise                                            # user-token cancellation, propagate as-is
        # Final check after clean source exhaustion (covers source.raise_on_cancel=False case).
        if self._active is not True:
            raise TransactionStoppedDuringIterationError(
                'Transaction was stopped while iteration was in progress.'
            )
        if self._change_alarm.is_alarmed():
            raise TransactionalChangeError(self._change_alarm.get_message())

    def _stop_safely_at_exit(self) -> None:
        # atexit fallback when `__exit__` was bypassed. Cleanup errors are
        # suppressed but emitted as ResourceWarning; default filters may hide
        # them, and warnings-as-errors may let warnings.warn escape.
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
        if not isinstance(transactional, bool):
            raise InvalidTransactionalArgumentError('The `transactional` argument must be a bool.')
        if transactional is False:
            raise InvalidTransactionalArgumentError(
                'Cannot pass transactional=False to TransactionalCrawler.apply(). '
                'TransactionalCrawler is always transactional; this kwarg is kept only for '
                'signature compatibility with non-transactional crawlers.'
            )
        if self._active is not True:
            raise TransactionInactiveError(
                'TransactionalCrawler can only be used inside an active transaction. '
                'Use `with crawler.transaction as scope:` and work with `scope` inside the block.'
            )
        PossibleCallMatcher('.').match(function, raise_exception=True)
        for path in self.go(token):
            function(path)
```

### `ChangeAlarm`

`ChangeAlarm` — единый thread-safe holder: `threading.Lock` для first-wins, `threading.Event` для `ConditionToken`, готовая `message: str`.

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
        # Lock-free hot path polled by ConditionToken. After `set_first()`,
        # the next token check must observe the Event flag; message visibility
        # is guaranteed by `get_message()` reading under `_lock`.
        return self._event.is_set()

    def wait(self, timeout) -> bool:
        return self._event.wait(timeout)
```

Публичный API `ChangeAlarm`: только `set_first`, `is_alarmed`, `wait`, `get_message`. `_do_record(message)` не публичный API, но это намеренный private test hook: он должен оставаться отдельным instance-replaceable методом, который `set_first()` вызывает уже под `self._lock`; сам `_do_record()` lock не берёт и только пишет `__message` + `_event.set()`. `__message` остаётся name-mangled (`_ChangeAlarm__message`); прямой `alarm._message` должен давать `AttributeError`. `set_first(path, event_type, dest_path=None)` принимает `str | Path` для path-аргументов и не нормализует их, только форматирует сообщение. `Endpoint.message_path: str | Path`, `Endpoint.filter_path: Path | None` — handler-internal helper, не часть API `ChangeAlarm`. `str | Path` / `Path | None` здесь псевдотипы плана; production-аннотации — Python 3.8-compatible.

### `FilteredEventHandler`

Handler создаётся для каждого leaf `Crawler`; file/missing base entries только исключаются из `scheduled_bases` и не schedule'ятся. Если у leaf `scheduled_bases == ()`, per-leaf handler всё равно создаётся один раз, но ни разу не передаётся в `observer.schedule()`; это входит в контракт, и structural-spy tests должны учитывать такие handlers, если в сценарии есть leaf без schedulable bases. `scheduled_bases` не дедуплицируется: сохраняет все элементы `leaf.paths`, являющиеся директориями на момент `_start()`, в исходном порядке после фильтра `Path(base).is_dir()`; ни lexical, ни resolve-based дедупликации нет, поэтому дубли и эквивалентные пути приводят к отдельному `observer.schedule(handler, str(base), recursive=True)` для каждого элемента. Если такой duplicate/equivalent `schedule()` бросает, это обычный failed setup raw-error path; manual dedup всё равно не добавлять. Handler единожды собирает `excludes_spec = source._compile_excludes()` и сохраняет snapshot-поля: `_bases = tuple((Path(base), Path(base).resolve(strict=False)) for base in scheduled_bases)`, `extensions = None | tuple(source.extensions)`, `filter = source.filter` (только текущая ссылка на callable: reassign `source.filter` handler'у не виден, внутреннее mutable state callable остаётся live), `only_files = source.only_files`; `scheduled_bases` — constructor-local input, отдельное `self._scheduled_bases` не нужно. `on_any_event` использует только эти поля, не читает live `source.*`; изменения конфигурации crawler во время активной транзакции не поддерживаются. В каждой паре `_bases` первый элемент — `crawler_base`, второй — precomputed `resolved_base`; `crawler_base` хранится как `Path(base)` без дополнительной lexical normalization, а порядок tuple используется для tie-break при равной длине matching base. Так path из watchdog приводится к той же relative/absolute форме, в какой `Crawler._traverse()` отдаёт `child_path`; для symlink descendants это не обещает lexical parity, действует `crawler-coordinate path`. На каждый event резолвится только `event_path`; `resolved_base` берётся из snapshot `_bases`. Membership и `relative_tail` вычислять именно через прямой `resolved_event.relative_to(resolved_base)` в `try/except ValueError`; ручной эквивалент не допускается. Symlink base paths не имеют отдельной гарантии для boundary delete/move: handler использует только best-effort resolve-based matching, без symlink-specific checks. Symlink descendants обрабатываются тем же handler-time/current-FS resolve-based matching без target cache: если resolved event path не находится под resolved_base, событие считается outside.

Алгоритм `on_any_event` — в «Классификации событий и фильтрации».

## Тестовая инфраструктура и конвенции

`CLAUDE.md` — создать при отсутствии; в разделе `## Test conventions` добавить/обновить **два** правила ниже, создав раздел при необходимости; не дублировать раздел и не удалять unrelated правила:
1. «Unit-тесты лежат в `tests/units/` и **зеркалят** структуру `dirstree/`: для исходника `dirstree/X/Y.py` тест — `tests/units/X/test_Y.py`. Test-specific helpers допустимы как fixtures/top-level helpers в `tests/conftest.py` или как локальные функции внутри тестов; для новых тестов module-level helpers в test modules запрещены, если исключение не названо явно. Existing migrated tests are grandfathered. Исключение: transactional feature tests that span `errors.py`, `abstract.py`, root re-exports, `apply()` and `ChangeAlarm` intentionally live in `tests/units/crawlers/transactional/test_crawler.py`».
2. «Тесты, проверяющие **инварианты**, на которые опирается реализация (свойства самого Python либо внешних зависимостей), лежат в `tests/python/` с двумя поддиректориями:
   - `tests/python/interpreter/` — инварианты CPython (атомарность операций, memory model и т.п.).
   - `tests/python/dependencies/` — инварианты конкретных внешних зависимостей (cantok, watchdog и т.п.).

   Структура внутри не зеркалит исходники: каждый файл соответствует **проверяемому свойству**, не модулю. Примеры: `tests/python/interpreter/test_atomicity.py`, `tests/python/dependencies/test_cantok.py`. В docstring'ах таких тестов обязательно: (а) на какую часть реализации опирается инвариант, (б) какое поведение сломается, если инвариант перестанет выполняться в будущей версии».
В `CLAUDE.md` эти правила писать на английском; русские формулировки выше задают смысл.

После переноса существующих тестов `tests/conftest.py` остаётся в `tests/` и доступен из всех subdirs; fixture paths сохраняют форму `tests/test_files/...` и работают при cwd = корень репозитория для `tests/units/...` и `tests/python/...`. Существующие переносимые тесты не переписывать под новый helper-стиль; новые transactional-тесты следуют правилу ниже.

Основной сьют — `tests/units/crawlers/transactional/test_crawler.py` (зеркало `dirstree/crawlers/transactional/crawler.py`). Соблюдаем конвенции репо: тесты — функции (не классы), у каждого docstring (первая строка — property, дальше — почему/как), никаких module-level хелперов внутри test module; module-level constants вроде `STRESS_REPEATS = 20` разрешены. Pytest fixtures и top-level helpers в `tests/conftest.py` разрешены, если helper нужен вне test module (например, picklable target для `multiprocessing`). `tmp_path` для мутирующих сценариев, `pytest.raises(..., match=match('точное'))` через `from full_match import match`, `pytest.mark.skipif` только на уровне декоратора. Синхронизация — `threading.Barrier` для детерминированной точки синхронизации и `_change_alarm.wait(timeout=2.0)` для ожидания асинхронной доставки; `_event` не является стабильным тестовым hook'ом. Все межпоточные ожидания (`Barrier.wait`, `Event.wait`, `_change_alarm.wait`, `future.result`, `join`) должны иметь конечный timeout; timeout/broken-barrier/worker exception должен попадать в main thread как test failure. Никогда не голый `sleep`. Исключения из worker-thread'ов обязательно возвращать в main thread через `ThreadPoolExecutor`/future или shared exception container + `join`; `assert` внутри worker без проверки в main не считается тестом. Доступ к `_change_alarm`, `_observer`, `_active` — приватные поля, к которым тесты намеренно обращаются; это документируется в комментарии у поля. Все новые test modules, включая `transactional_runtime`, должны импортироваться/collect'иться на Python 3.8 без transactional extra: без module-level watchdog imports, без runtime-evaluated 3.9+/3.10+ typing syntax; если нужен `list[str]`/`str | Path`, использовать `from __future__ import annotations` или `typing.*`.

### Test infrastructure (`_change_alarm.wait()` + sentinel-pattern)

**`_change_alarm.wait(timeout=2.0)`** используется для детерминированного ожидания цепочки `мутация → watchdog handler → ChangeAlarm.set_first`. Возврат всегда проверяется ассертом, а docstring теста говорит, какое событие ждём.

Если тест запускает настоящий `_start()` / `with ...transaction` / `apply(transactional=True)` и `_load_watchdog` не полностью fake, он обязательно помечается `@pytest.mark.transactional_runtime`; полностью fake = тест не вызывает original `_load_watchdog`, не импортирует watchdog и fake `_load_watchdog` возвращает и fake `Observer`, и fake `FilteredEventHandler`. Любой wrapper/частичный fake вокруг real watchdog считается `transactional_runtime`. Direct-handler/fake-event subcase, если явно не сказано «полностью fake», берёт real `FilteredEventHandler` из original `_load_watchdog()` и тоже считается `transactional_runtime`; fake event подаётся в реальный handler, а не в тестовый заменитель handler-логики. Если test function содержит хотя бы один такой real direct-handler subcase, вся function маркируется `transactional_runtime` (например, #18, #20-23, #71, #72). Исключение: dependency-error/version-gate тесты, которые гарантированно не импортируют watchdog успешно и не стартуют реальный Observer (например, #33), не маркируются `transactional_runtime`, чтобы выполняться в compatibility CI. Во всех manual `_start()`-тестах, если TC мог стать active, cleanup обязан идти через `try/finally` и настоящий `_stop()`.

Setup/start failure coverage: production ветки failed setup/start, достижимые через fake `_load_watchdog`/fake Observer без real watchdog, должны иметь таргетированный fake-failure subcase в transactional test suite или явно попадать под существующий failure-path тест. Разделять cleanup expectations: `Observer()` и `observer.daemon = True` failures запускают no cleanup, fake logs показывают отсутствие `stop`/`join`; handler init/`_compile_excludes()`/`resolve`/`observer.schedule` failures идут через schedule-cleanup `stop()`/`join()` before start, причём fake logs детально ассертятся хотя бы в одном representative subcase; `observer.start`/`atexit.register` failures идут через `_start()` cleanup. Для `observer.start`/`atexit.register` failure paths `stop` under lock и `join` outside lock проверяются targeted failure-log assertion с lock-state capture; #14 покрывает normal `_stop()` join outside lock. Fake `atexit.register`, который падает, моделирует failure без side effects: hook не считается registered и `unregister` не ожидается. Setup/start cleanup `stop`/`join` failures покрываются отдельным warning-as-error/resource-warning subcase; normal `_stop()` `observer.stop()` failure покрывается отдельным normal-stop subcase: exception propagates, `_active is True`, atexit unregister spy not called / hook remains registered. Отдельный numbered test для каждого пункта не требуется; unnumbered subcases в `test_crawler.py` допустимы, если они покрывают перечисленные assertion-группы и проходят 100% coverage gate.

Если пользовательский `filter` в тесте имеет side effects для синхронизации/счётчика, эти side effects выполняются только в потоке обхода (`threading.current_thread() is main_thread`); watchdog-thread branch пропускает только sync/counter side effects, но всё равно вычисляет predicate по path. `True` возвращают только purely-sync filters без predicate-логики. Это не относится к тестам аргументов handler-фильтра: они отдельно записывают watchdog-thread вызовы и помечают их по thread id. Каждый mutating/sentinel subcase, даже если он описан без явного `inner for-loop`, создаёт fresh subtree или полностью пересобирает fixture state и новый crawler/TC; direct-handler fake-event subcase, проверяющий `set_first`, создаёт fresh `ChangeAlarm` и fresh handler на каждый fake event.

**Sentinel-pattern** — для детерминированной проверки «событие X **не** trip'нуло, потому что было отфильтровано». Внутри активного `with` main запускает worker и удерживает traversal живым; каждый sentinel subcase обязан иметь stable yielded path и main-thread `filter`/callback/`Barrier`, который не отпускает обход до `scope._change_alarm.wait(timeout=2.0)` по sentinel. Worker через `Barrier` сначала trigger'ит **ignored**-событие (path, отсекаемый фильтрами), потом сразу **sentinel**-событие (path, проходящий фильтры). Главный поток `assert scope._change_alarm.wait(timeout=2.0)` ждёт первый alarm: при корректном поведении это sentinel, а если ignored ошибочно trip'нул раньше, это выявляется проверкой message. Проверка: `scope._change_alarm.get_message()` содержит sentinel, **не** ignored (если бы ignored trip'нул, first-wins зафиксировал бы его до sentinel'а). Runtime sentinel доказывает отсутствие false-positive только если ignored event реально доставлен до sentinel; sentinel-only subcase без записи watchdog-thread обработки ignored event не доказывает доставку ignored event и должен иметь direct-handler companion. Точная filter-проверка для строгих filter-contract tests должна иметь direct-handler/fake-event subcase или отдельную запись watchdog-thread вызова filter'а. После проверки message выход из `with` в sentinel subcase должен быть обёрнут в `pytest.raises(TransactionalChangeError)` или явно ловить этот clean-`__exit__` final raise. Допущение: последовательные FS-мутации от одного worker'а доставляются handler'у в том же порядке и `watchdog.Observer` обрабатывает их одним dispatcher-потоком; эти инварианты покрыты `test_observer_single_dispatcher_processes_events_sequentially` (см. impact-описание там же). Во всех sentinel subcase ignored и sentinel создаются внутри одного scheduled watched base/одного emitter; cross-leaf и cross-base ordering, включая multi-path `Crawler`, не использовать.

**Кэш `_load_watchdog` между тестами.** `_load_watchdog` — process-level `lru_cache`; тесты с monkeypatch `_load_watchdog` должны через autouse fixture в `tests/conftest.py` сохранять original `_load_watchdog` до monkeypatch, вызывать `original_load_watchdog.cache_clear()` до теста, а после `yield` снова чистить именно сохранённый original cache, не полагаясь на текущий class attribute. В смешанных модулях `transactional_runtime` marker ставится на function-level, а module-level marker допустим для `tests/python/dependencies/test_watchdog.py`.

Monkeypatch `_load_watchdog`: на class-level назначать `staticmethod(fake_load_watchdog)`, иначе function станет bound method и получит лишний `self`; на instance-level `instance.method = spy` автобиндинга `self` нет, поэтому `spy` пишется без `self` (это instance-spy, не class override).

### Платформенные skip-ы

**Правило**: preemptive skip'ов нет; `pytest.mark.skipif(...)` допускается только после воспроизведённого platform-specific failure/flakiness с явной ссылкой на issue/bug-репорт.

Известные платформенные особенности:
- Windows ReadDirectoryChangesW шлёт дубликаты `modified`-событий ([watchdog #346](https://github.com/gorakhargosh/watchdog/issues/346)) — `ChangeAlarm` first-wins иммунен.
- Free-threading 3.14t: статус watchdog'а публично не задокументирован — проверить в момент имплементации; если `pip install -e '.[transactional]'` или transactional runtime tests падают, соответствующий matrix entry остаётся failing. Skip/exclude допускается только через documented matrix/test-plan change с причиной. То же no-silent-skip/exclude правило действует для всех non-3.8 matrix entries, включая `3.15.0-alpha.1`.

## TDD-стратегия

Каждый numbered core/invariant test ниже: **имя → property → как проверяется**; исключение #12-16 описано у таблицы. `inner for-loop` = обычный `for` внутри одного pytest-теста, не `@pytest.mark.parametrize`.

### Базовая корректность

**1. `test_transactional_yield_set_is_identical_to_plain_crawl_when_tree_is_static_for_all_source_types`**
- *Property:* static tree: `TransactionalCrawler` совпадает с plain traversal для `Crawler`, `PythonCrawler`, `CrawlersGroup`; порядок игнорируется, дубли проверяются по текущей source-семантике.
- *Как:* inner for-loop по source-сценариям:
  ```python
  # tmp_path заполнен: a.py, b.py, c.txt, sub/d.py, sub/e.txt
  scenarios = [
      ('Crawler',         lambda: Crawler(tmp_path)),
      ('PythonCrawler',   lambda: PythonCrawler(tmp_path)),
      ('CrawlersGroup',   lambda: Crawler(tmp_path / 'sub') + Crawler(tmp_path, exclude=['**/sub/**'])),
  ]
  for source_name, build_source in scenarios:
      source = build_source()
      expected = sorted(source.go())
      with source.transaction as scope:
          actual = sorted(scope)
      assert actual == expected, f'mismatch for {source_name}'
  ```
**2. `test_transaction_property_returns_fresh_instance_each_access`**
- *Property:* `crawler.transaction` каждый раз создаёт новый `TransactionalCrawler`.
- *Как:* `first = crawler.transaction; second = crawler.transaction`. Проверить `first is not second`, `isinstance(first, TransactionalCrawler)`, `first.source is crawler`, `first._active is None`.

**3. `test_enter_exit_lifecycle_through_all_scenarios`**
- *Property:* Контекст-менеджер полностью покрыт: (a) `__enter__` возвращает self и активирует TC с живым Observer; (b) нормальный `__exit__` останавливает Observer, переводит в `_active=False`, возвращает None; (c) исключение из тела `with` (как до итерации, так и в середине) пробрасывается наружу, и Observer остановлен, если `_stop()` дошёл до stopped-state; early `_stop()` failures могут оставить observer active и покрываются отдельно; (d) если spy поднимает ошибку после успешного `_stop()` во время body-exception, body-exception не маскируется, cleanup error уходит в `ResourceWarning`.
- *Как:* inner for-loop по сценариям `noop`, `raise_after_enter`, `raise_mid_iter` (`next(iter(scope))`, потом `RuntimeError('boom')`). Для `raise_mid_iter` fixture создаёт минимум один реально yieldable path; перед `RuntimeError` проверить, что `next(...)` вернул path. Для каждого сценария сначала явно создать `tc = crawler.transaction`, затем `with tc as scope:`. Внутри `with` проверить `scope is tc`, `_active is True`, observer alive; после нормального и exception-выхода, где `_stop()` дошёл до stopped-state, проверить `_active is False`, observer dead, body-exception проброшен. Отдельный subcase monkeypatch'ит `tc._stop` instance-level spy без `self`: сохранить bound `original = tc._stop`, `spy()` вызывает `original()` и затем поднимает stop-error; внутри `with pytest.warns(ResourceWarning)` проверить, что наружу вышел `RuntimeError('boom')`, не stop-error.

**4. `test_cached_properties_return_same_instance_on_multiple_accesses`**
- *Property:* `_observer` и `_change_alarm` — оба `cached_property`: повторные обращения возвращают тот же экземпляр.
- *Как:* fully fake `_load_watchdog`. Inner for-loop по `_observer` и `_change_alarm`; внутри каждой итерации создавать fresh `tc = crawler.transaction`, затем `tc._start()`, два обращения к атрибуту возвращают один объект через `is`, затем `tc._stop()`.

**5. `test_observer_attribute_persists_after_stop_but_thread_is_dead`**
- *Property:* После `stop()` атрибут `_observer` сохраняется в `tc.__dict__` (cached_property не очищается), но сам поток Observer мёртв.
- *Как:* `tc = crawler.transaction; tc._start(); observer_ref = tc._observer; tc._stop(); assert tc._observer is observer_ref; assert observer_ref.is_alive() is False`.

### Inactive / lifecycle state

**6. `test_inactive_tc_iteration_methods_all_raise_inactive_error`**
- *Property:* Любой метод итерации (`go`, `__iter__`, `apply` с валидным/default `transactional`, независимо от валидности `function`) на свежем TC без `__enter__`/`_start()` кидает `TransactionInactiveError` с одинаковым (общим) текстом инструкции; validation cases для `TC.apply(transactional=False/non-bool)` покрыты в #49.
- *Как:* inner for-loop по операциям `next(tc.go())`, `list(tc)`, `tc.apply(lambda path: None)`, `tc.apply(object())`; для каждой операции использовать свежий TC и ожидать общий `TransactionInactiveError` с текстом `TransactionalCrawler can only be used inside an active transaction. Use \`with crawler.transaction as scope:\` and work with \`scope\` inside the block.`

**7. `test_reuse_scope_after_with_exit_raises_inactive_error`**
- *Property:* Сохранённая снаружи ссылка на `scope` после выхода из `with` нерабочая — гарантируем, что TC не оживает.
- *Как:* `with crawler.transaction as scope: gen = scope.go()`. После блока `list(scope)` и `next(gen)` оба дают `TransactionInactiveError` (для заранее созданного generator — на первом `next()`, не при создании).

**8. `test_lifecycle_invalid_transitions_raise_per_state`**
- *Property:* Невалидные lifecycle-переходы кидают конкретные исключения per-state. Покрывает все запрещённые комбинации `(initial_state, operation)`.
- *Как:* inner for-loop по таблице:

  | Setup | Operation | Exception | Message |
  | --- | --- | --- | --- |
  | active (`_start()`) | `_start()` | `TransactionAlreadyActiveError` | `TransactionalCrawler is already active.` |
  | stopped (`_start(); _stop()`) | `_start()` | `TransactionInactiveError` | `TransactionalCrawler has already been stopped and cannot be restarted. Create a new one via original_crawler.transaction.` |
  | fresh | `_stop()` | `TransactionInactiveError` | `Cannot stop a TransactionalCrawler that has never been started.` |
  | stopped (`_start(); _stop()`) | `_stop()` | `TransactionInactiveError` | `TransactionalCrawler has already been stopped.` |

  Active setup subcase использует `try/finally`: после проверки повторного `_start()` обязательно вызвать настоящий `_stop()`, если TC всё ещё active.

### Event detection / ChangeAlarm / lifecycle-lock

**9. `test_each_file_event_type_during_iteration_raises_with_correct_message`**
- *Property:* Direct-handler path записывает exact `ChangeAlarm` message для каждого из четырёх supported file-event types (`created`, `deleted`, `moved`, `modified`); conversion в `TransactionalChangeError` проверяется через `TC.go()`/runtime path.
- *Как:* разделить на два subcase для каждого сценария. (a) Точный `event_type`/message проверять через real `FilteredEventHandler`, полученный из `TransactionalCrawler._load_watchdog()`, real `ChangeAlarm` и простой fake event object; direct-handler создаёт `handler = Handler(crawler, alarm, scheduled_bases=(tmp_path,))`, вызывает `handler.on_any_event(fake_event)`, затем проверяет `alarm.is_alarmed()` и `alarm.get_message() == expected(tmp_path)`. Fake file events задают `is_directory=False`; fake `moved` event задаёт и `src_path`, и `dest_path`. Direct-handler/fake-event subcase использует отдельный crawler без blocking sync-filter (например, `filter=lambda _: True`) или вызывает handler из non-main thread; blocking `filter_` ниже только для runtime-subcase. Так exact-check не зависит от порядка real watchdog events вроде `touch()` → `created`/`modified`. (b) Runtime-путь через реальный watchdog покрывает доставку события во время активного обхода, но проверяет только сам `TransactionalChangeError`, без точного `event_type`/message. Для deleted/moved ordering ниже нужен только чтобы обойти pre-existing `Crawler._traverse` race. Inner for-loop по сценариям. **Ordering для deleted/moved**: мутация делается **после** того, как `_traverse` уже выполнил stat-dependent checks для целевого файла; это временный обход бага `Crawler._traverse` (см. `issue_self.md` и «Известные ограничения», п.2). Для `created` синхронизация идёт по заранее существующему `guard.txt`, потому что `new.txt` ещё не может быть yield'нут. Worker ждёт `yielded_sync.wait(timeout=2.0)` перед мутацией; затем ждёт `_change_alarm.wait(timeout=2.0)` чтобы гарантировать доставку события (детерминирует тест на slow CI):
  ```python
	  scenarios = [
	      ('created',  'guard.txt',    lambda tmp: (tmp/'new.txt').touch(),
	                   lambda tmp: f'Directory changed during transactional iteration: created at {tmp / "new.txt"}'),
	      ('deleted',  'existing.txt', lambda tmp: (tmp/'existing.txt').unlink(),
	                   lambda tmp: f'Directory changed during transactional iteration: deleted at {tmp / "existing.txt"}'),
	      ('moved',    'a.txt',        lambda tmp: (tmp/'a.txt').rename(tmp/'b.txt'),
	                   lambda tmp: f'Directory changed during transactional iteration: moved from {tmp / "a.txt"} to {tmp / "b.txt"}'),
	      ('modified', 'existing.txt', lambda tmp: (tmp/'existing.txt').write_text('changed'),
	                   lambda tmp: f'Directory changed during transactional iteration: modified at {tmp / "existing.txt"}'),
	  ]
	  for event_name, sync_name, mutation, expected in scenarios:
      root = tmp_path / event_name
      root.mkdir()
      # setup fresh root: created => guard.txt exists/new.txt absent;
      # deleted/modified => existing.txt exists; moved => a.txt exists/b.txt absent;
      # plus non-target files
      yielded_sync = threading.Event()
      event_delivered = threading.Event()
      main_thread = threading.current_thread()
      def filter_(path):
          if threading.current_thread() is main_thread and path.name == sync_name:
              yielded_sync.set()          # signal worker: sync point passed _traverse's is_file()
              assert event_delivered.wait(timeout=2.0)
          return True
      def worker():
          assert yielded_sync.wait(timeout=2.0)
          mutation(root)
          assert scope._change_alarm.wait(timeout=2.0)   # ensure event delivery before main proceeds
          event_delivered.set()
      crawler = Crawler(root, filter=filter_)
      with ThreadPoolExecutor(max_workers=1) as executor:
          future = executor.submit(worker)
          with pytest.raises(TransactionalChangeError):
              with crawler.transaction as scope:
                  list(scope)
          future.result(timeout=5.0)
  ```

**10. `test_first_event_wins_subsequent_changes_do_not_overwrite_message`**
- *Property:* Integration-проверка first-wins через реальный watchdog и две реальные ФС-мутации.
- *Как:* per-instance spy на `scope._change_alarm.set_first`: инкрементит `call_count`, делегирует в original `set_first`, и ставит `second_call_seen` только когда path/message относится к `second.txt`. Один worker последовательно делает оба touch (`first.txt`, затем `second.txt`) внутри одного scheduled watched base/одного emitter; два `Barrier(2)` разделяют эти мутации. Main удерживает traversal внутри активного `with` на blocking filter/callback до `second_call_seen`: ждёт первый alarm, сохраняет `first_message`, отпускает вторую мутацию, ждёт `second_call_seen`, проверяет `get_message() == first_message`; только после этой проверки отпускает traversal, и выход из `with` поднимает `TransactionalChangeError` с `first.txt`.

**11. `test_change_alarm_subsequent_set_first_does_not_overwrite_message`**
- *Property:* Повторный `set_first()` не меняет первое сообщение.
- *Как:* `alarm = ChangeAlarm(); alarm.set_first(Path('/a'), 'created'); first_msg = alarm.get_message(); alarm.set_first(Path('/b'), 'deleted'); assert alarm.get_message() == first_msg`.

**LockTrace-тесты lifecycle (#12-16)** используют общий шаблон: `_lifecycle_lock` оборачивается в `LockTraceWrapper`, конкретный fake/spy метод из таблицы делает `notify(...)`, затем проверяется `was_event_locked(...)`. Каждая строка таблицы ниже — отдельная test function с указанным именем.

| Тест | Traced event | Ожидание |
| --- | --- | --- |
| **12. `test_observer_start_happens_under_lifecycle_lock`** | `Observer.start()` → `observer_start_call` | under `_lifecycle_lock` |
| **13. `test_observer_stop_happens_under_lifecycle_lock`** | `Observer.stop()` → `observer_stop_call` | under `_lifecycle_lock` |
| **14. `test_observer_join_happens_OUTSIDE_lifecycle_lock`** | `Observer.join()` в нормальном `_stop()` → `observer_join_call` | outside `_lifecycle_lock` |
| **15. `test_observer_cached_property_first_access_under_lifecycle_lock`** | fake `_load_watchdog()` → `observer_first_init` перед возвратом fake classes | under `_lifecycle_lock` |
| **16. `test_change_alarm_cached_property_first_access_under_lifecycle_lock`** | spy на `ChangeAlarm.__init__` → `change_alarm_first_init` | under `_lifecycle_lock` |

**17. `test_change_alarm_set_first_writes_data_under_lock`**
- *Property:* В `ChangeAlarm.set_first()` запись `self.__message` и `self._event.set()` происходят под `_lock` (контракт first-wins).
- *Как:* `alarm = ChangeAlarm(); alarm._lock = LockTraceWrapper(alarm._lock)`. Подменить `_do_record` **на инстансе** (`alarm._do_record = traced`); обёртка делает `alarm._lock.notify('write')` перед делегированием. После `alarm.set_first(...)` — `alarm._lock.was_event_locked('write', raise_exception=True)`.

### Filtering

**18. `test_filtered_event_does_not_trip_iteration`**
- *Property:* Событие, отфильтрованное `extensions`, не становится first alarm; sentinel-событие намеренно trip'ит транзакцию.
- *Как:* sentinel-pattern: `extensions=['.py']`: `noise.txt` → `sentinel.py`. Добавить direct-handler/fake-event subcase на ignored path: fresh alarm+handler, `is_directory=False`, после `handler.on_any_event(fake_event)` alarm не установлен.

**19. `test_filter_exception_in_event_thread_is_treated_as_trip`**
- *Property:* Если пользовательский `filter` падает с исключением в фоновом потоке handler-а — это считается trip-ом (консервативный дефолт).
- *Как:* filter падает только в watchdog thread: `main_thread = threading.current_thread(); def filter_(path): if threading.current_thread() is not main_thread and path.name == 'boom.txt': raise ValueError('oops'); return True`. Главный поток в transaction блокируется filter/barrier на стабильном path; worker создаёт `boom.txt`, assert'ит `scope._change_alarm.wait(timeout=2.0)`, затем отпускает traversal. Ожидаем `TransactionalChangeError`, не raw `ValueError` из обычной итерации.

**20. `test_exclude_pattern_matches_deeply_nested_paths_in_handler`**
- *Property:* `exclude=['**/build/**']` рекурсивно фильтрует все вложенные пути при absolute `tmp_path` (поведение `pathspec.gitwildmatch`). Проверяем, что handler применяет exclude на любой глубине.
- *Как:* sentinel-pattern + direct-handler/fake-event ignored check. `Crawler(tmp_path, exclude=['**/build/**'])`. Setup: `tmp_path/'build'/'level1'/'level2'/` (mkdir). Worker трогает `build/level1/level2/deep.txt` (excluded), затем `sentinel.txt` (allowed). Проверка: `get_message()` содержит `sentinel.txt`, не `deep.txt`. Direct-handler subcase вызывает handler на fake `created` event для `build/level1/level2/deep.txt` и проверяет, что alarm не установлен.

**21. `test_multiple_exclude_patterns_all_apply_in_handler`**
- *Property:* Несколько exclude-pattern'ов (`exclude=['**/build/**', '*.tmp', '**/docs/**']`) применяются как OR: событие, попавшее в любой pattern, игнорируется.
- *Как:* sentinel-pattern + direct-handler/fake-event ignored checks. Setup: `(tmp_path/'build').mkdir(); (tmp_path/'docs').mkdir()`. Worker через barrier по очереди: `build/x.txt`, `foo.tmp`, `docs/a.txt` (все excluded по разным pattern'ам), затем `sentinel.txt`. Проверка: `get_message()` содержит `sentinel.txt`; не содержит exact ignored basenames/event paths (`x.txt`, `foo.tmp`, `a.txt`), а не произвольные substrings из `tmp_path`. Direct-handler subcases отдельно проверяют fake `created` event для каждого ignored path и assert alarm не установлен.

**22. `test_multiple_extensions_all_recognized_in_handler`**
- *Property:* `extensions=['.py', '.txt']` — handler trip'ит на файле с **любым** из перечисленных расширений.
- *Как:* sentinel-pattern с двумя fresh подсценариями: (1) `noise.md` (ignored) → `sentinel.py` (trip); (2) `other.css` (ignored) → `sentinel.txt` (trip). Direct-handler subcases отдельно проверяют fake `created` events для `noise.md` и `other.css` и assert alarm не установлен.

**23. `test_directory_event_is_ignored_when_only_files_true_except_watched_base_boundary`**
- *Property:* При `only_files=True` (default) обычные directory-events не триггерят; handler не сканирует содержимое moved/deleted directory и не подавляет отдельно доставленные descendant file events. Исключение: `deleted`/`moved` с boundary endpoint — boundary-событие и оно trip'ит.
- *Как:* (1) sentinel-pattern: worker делает `(tmp_path/'new_dir').mkdir()`, затем `(tmp_path/'sentinel.txt').touch()`; message содержит `sentinel.txt`, не `new_dir`. (2) Direct handler subcase: `_, Handler = TransactionalCrawler._load_watchdog(); scheduled_bases = (tmp_path,)`; fake event имеет `event_type`, `src_path`, `is_directory`, а для moved ещё `dest_path`; для watched-base `created`/`modified`/`deleted` и boundary `moved` fake events задавать `is_directory=True`. Для каждого fake event создать fresh `alarm = ChangeAlarm()` и fresh `handler = Handler(crawler, alarm, scheduled_bases)`. Проверить: ordinary non-boundary directory `created`/`modified`/`deleted`/`moved` под watched base при `is_directory=True` и `only_files=True` не ставят alarm; ordinary `created`/`modified` boundary endpoint не ставят alarm; `deleted` с boundary endpoint, `moved` boundary → outside (`src` boundary, `dest` outside) и `moved` outside → boundary (`src` outside, `dest` boundary) вызывают `set_first`, несмотря на `only_files=True`.

### Multiple iterations / Observer lifecycle

**24. `test_observer_lives_whole_transaction_catches_change_between_iterations`**
- *Property:* Внутри одного `with` Observer живёт всё время → изменение между двумя последовательными итерациями ловится во второй.
- *Как:* `pytest.raises(TransactionalChangeError)` оборачивает весь `with crawler.transaction as scope:`; внутри: `list(scope); (tmp_path/'new').touch(); assert scope._change_alarm.wait(timeout=2.0); next(iter(scope))`. Это покрывает already-alarmed pre-source path.

**25. `test_two_sequential_with_blocks_on_same_crawler_are_isolated`**
- *Property:* После выхода из первого `with` (даже сорванного) состояние не наследуется во второй.
- *Как:* первый with провоцирует ChangeError через worker и barrier. После — `assert scope_first._observer.is_alive() is False and scope_first._active is False`. Чистим дерево обратно. Второй with на чистом дереве проходит до конца. Проверяем, что второй `scope_second is not scope_first` и `scope_second._change_alarm is not scope_first._change_alarm` (разные TC, разные ChangeAlarm'ы).

**26. `test_observer_stopped_when_generator_is_garbage_collected_mid_iteration`**
- *Property:* Если генератор `scope.go()` создан, частично исчерпан и брошен — Observer не останавливается преждевременно из-за `GeneratorExit`; он живёт до `__exit__` контекстника и затем завершается.
- *Как:* fixture содержит минимум два yielded path, чтобы после `next(gen)` generator ещё не был exhausted. Внутри `with crawler.transaction as scope:` сделать `gen = scope.go(); next(gen); del gen; gc.collect(); assert scope._observer.is_alive() is True`, затем создать новый iterator из того же `scope` и проверить, что он ещё usable на static tree. После выхода из `with` проверить `scope._observer.is_alive() is False`.

### Thread safety

**27. `test_two_threads_each_with_own_transaction_are_fully_isolated`**
- *Property:* Два параллельных `with`-блока в разных потоках имеют независимые TC-ресурсы и фильтры. Реальные ФС-изменения в общей директории могут trip'нуть обе транзакции; это не shared-state leakage.
- *Как:* два разных `Crawler` на одной `tmp_path` с **разными** `exclude` (`A: exclude=['**/part_a/**']`, `B: exclude=['**/part_b/**']`). До входа в транзакции создать директории `part_a` и `part_b`; внутри транзакций только создавать/изменять файлы в них. Каждый поток держит свой `scope` в локальной переменной, после active-barrier остаётся внутри `with` на `release` event и возвращает исключение/сообщение в main thread. Main хранит refs на оба `scope`: после active-barrier мутирует `part_a/x` (ignored by A, visible to B) и ждёт alarm на scope B, затем мутирует `part_b/x` (ignored by B, visible to A) и ждёт alarm на scope A, после этого выставляет `release`. Каждый worker явно продвигает iterator или выходит из `with` под `pytest.raises(TransactionalChangeError)`. Проверить оба errors и пути в message (`A` видит `part_b/x`, `B` видит `part_a/x`).

**28. `test_two_concurrent_transactions_have_distinct_observers_and_events`**
- *Property:* Внутренние ресурсы (Observer, Event, ChangeAlarm) у двух одновременно активных TC — разные объекты.
- *Как:* запустить два потока, каждый делает `tc = crawler.transaction; tc._start()`, сохраняет `tc`, синхронизируется через `Barrier(3)` и ждёт `release` event. Главный поток тоже вызывает этот barrier после того, как оба worker'а сохранили свои TC; затем проверяет оба TC пока они active: `assert tc1._observer is not tc2._observer` и `assert tc1._change_alarm is not tc2._change_alarm` (поскольку Event живёт внутри ChangeAlarm — этого достаточно). Затем main выставляет `release`; каждый worker в `finally` вызывает свой настоящий `tc._stop()`, после чего main делает join/future.result().

### Plain crawler apply (#29-32)

**29. `test_apply_transactional_true_smoke_without_changes`**
- *Property:* `crawler.apply(fn, transactional=True)` без изменений отрабатывает корректно и вызывает callback для каждого пути.
- *Как:* заполнить дерево, `collected = []; crawler.apply(collected.append, transactional=True)`. `assert sorted(collected) == sorted(expected)`.

**30. `test_apply_transactional_true_raises_on_change_and_stops_calling_function`**
- *Property:* При изменении **между путями** во время `apply(transactional=True)` (после callback #1, до callback #2): поднимается `TransactionalChangeError`, callback вызывается **ровно один раз** — `_check_token` срабатывает на condition_token до следующего yield, последующие callback'и не запускаются. Покрывает раннюю мутацию. Отдельный сценарий `test_mutation_during_last_element_*` (#51) покрывает мутацию **после последнего** callback'а: там все N callback'ов успевают отработать до раиза.
- *Как:* docstring теста должен объяснить, что для детерминированного `len(collected) == 1` нужны две точки синхронизации: `Barrier` для callback#1 ↔ worker и `_change_alarm.wait()` для момента «event реально доставлен handler'у». Одного barrier недостаточно: между worker'овским `touch()` и видимостью alarm-cancellation в condition_token есть OS→dispatcher→handler-окно, в которое callback#2 может проскочить. Callback после barrier ждёт `release_callback.wait(timeout=2.0)`; worker делает touch → ждёт `_change_alarm.wait()` (alarm set) → `release_callback.set()`. Следующий `next()` гарантированно видит cancellation. Setup обязан создать минимум два yield-path, чтобы существовал callback #2, который должен быть предотвращён cancellation. Тестировать `crawler.apply(callback, transactional=True)` напрямую. Через monkeypatch property на `type(crawler).transaction` захватить TC, созданный внутри `apply()`: сохранить original property, в replacement вызвать `original.fget(self)`, положить TC в `captured_tc` и вернуть его. Worker запускается до `crawler.apply(...)`; callback #1 и worker встречаются на `Barrier(2)`, затем callback ждёт `release_callback`. Worker делает `touch()`, ждёт `captured_tc[0]._change_alarm.wait(timeout=2.0)`, затем `release_callback.set()`. Ожидаем `TransactionalChangeError` и `len(collected) == 1`.

**31. `test_apply_transactional_true_stops_observer_when_callback_raises`**
- *Property:* Если callback в `apply(transactional=True)` кидает исключение, Observer всё равно корректно остановлен (через `__exit__` внутреннего `with`).
- *Как:* setup создаёт минимум один yielded file, чтобы callback точно был вызван. Monkeypatch `_load_watchdog` на fully fake Observer/Handler: fake `Observer` собирает instances в `observer_instances`, `start()` ставит alive, `stop()` снимает alive, `join()` no-op, `is_alive()` возвращает state. Callback кидает `RuntimeError('boom')`; после `pytest.raises` проверить `observer_instances[-1].is_alive() is False`.

**32. `test_non_transactional_operations_never_load_watchdog`**
- *Property:* Операции, которые НЕ должны затрагивать transactional-логику, не импортируют и не дёргают watchdog.
- *Как:* monkeypatch `_load_watchdog` на `staticmethod(lambda: (_ for _ in ()).throw(RuntimeError('must not be called')))`, не на exception object; триггеры `c.apply(lambda p: None)`, `c.transaction`, `repr(c.transaction)` не должны raise.

### Errors / dependency

**33. `test_load_watchdog_raises_clear_error_when_python_too_old_or_watchdog_missing`**
- *Property:* `_load_watchdog()` поднимает `WatchdogNotInstalledError` с одинаковым install-hint в двух случаях: Python <3.9 (явный version gate до import watchdog) и Python >=3.9 без установленного watchdog (реальный `try/except ImportError → raise WatchdogNotInstalledError` путь).
- *Как:* Тест **не** помечается `transactional_runtime`: он не импортирует настоящий watchdog и безопасен для compatibility job. На Python >=3.9 сначала покрыть version-gate без старого интерпретатора: `monkeypatch.setattr(sys, 'version_info', (3, 8, 0))`, `TransactionalCrawler._load_watchdog.cache_clear()`, вызвать `tc._start()` и ожидать `WatchdogNotInstalledError`; на реальном Python 3.8 та же ветка покрывается без monkeypatch. Затем симулировать Python >=3.9 без пакета через `monkeypatch.setattr(sys, 'version_info', (3, 9, 0))` и `sys.modules[name] = None`, снова чистить `_load_watchdog` cache и ожидать тот же `WatchdogNotInstalledError`. Каждый subcase создаёт fresh `tc = crawler.transaction` после `cache_clear()`; TC после failed `_start()` не переиспользовать, потому что он terminal:
  ```python
  import sys
  monkeypatch.setitem(sys.modules, 'watchdog', None)
  monkeypatch.setitem(sys.modules, 'watchdog.observers', None)
  monkeypatch.setitem(sys.modules, 'watchdog.events', None)
  TransactionalCrawler._load_watchdog.cache_clear()
  tc = crawler.transaction
  with pytest.raises(WatchdogNotInstalledError,
                     match=match('watchdog is not installed. Transactional mode requires Python >=3.9 and the transactional extra: pip install dirstree[transactional]')):
      tc._start()
  assert tc._active is False
  with pytest.raises(TransactionInactiveError):
      tc._start()
  ```

**34. `test_transactional_change_error_is_not_subclass_of_cancellation_error`**
- *Property:* `TransactionalChangeError` не наследуется от `CancellationError`.
- *Как:* `assert not issubclass(TransactionalChangeError, CancellationError)`.

### Existing options, token semantics and group behavior

#### Existing options (#35-36)

**35. `test_frozen_plus_transactional_raises_when_change_happens_during_snapshot_build`**
- *Property:* При `freeze=True` и transactional, изменение во время сбора snapshot тоже срабатывает.
- *Как:* inner for-loop по двум source-классам; каждая итерация создаёт fresh subtree, crawler, TC, counters и sync primitives. Filter predicate в этом тесте возвращает `True` для всех stable paths и используется только для sync/counter side effects. Считать `expected_paths = list(crawler_for_count)` на отдельном side-effect-free crawler с той же predicate-семантикой: это yieldable stable paths для текущего `source_class`; затем `expected_count = len(expected_paths)` и assert `expected_count >= 2`. Синхронизацию делать по счётчику main-thread filter calls: `filter_with_barrier` блокирует первый main-thread call, а не path из отдельного обхода. Worker мутирует path, проходящий фильтры текущего source_class, и ждёт доставки alarm; filter не возвращает управление до подтверждения доставки. Поэтому snapshot не успевает пройти все `expected_count` path:
  ```python
  for source_class in [Crawler, PythonCrawler]:
      crawler = source_class(tmp_path, freeze=True, filter=filter_with_barrier)
      with pytest.raises(TransactionalChangeError):
          with crawler.transaction as scope: list(scope)
      assert yielded_count < expected_count
  ```

**36. `test_raise_on_cancel_false_does_not_silence_transactional_change`**
- *Property:* Даже при `raise_on_cancel=False` на source-краулере, TC всё равно поднимает `TransactionalChangeError`.
- *Как:* inner for-loop по двум source-классам:
  ```python
  for source_class in [Crawler, PythonCrawler]:
      crawler = source_class(tmp_path, raise_on_cancel=False, filter=blocking_filter)
      # fixture has a stable yielded path. Main-thread blocking_filter blocks
      # traversal; worker waits for that signal, mutates an allowed path
      # (for example new.py for PythonCrawler), waits for
      # scope._change_alarm.wait(timeout=2.0), then releases traversal.
      with pytest.raises(TransactionalChangeError):
          with crawler.transaction as scope: list(scope)
  ```

#### Token semantics / `raise_on_cancel` (#37-39)

**37. `test_external_cancellation_token_propagates_through_transaction_as_cancellation_error`**
- *Property:* Если cancellation приходит **извне** (от source-токена в конструкторе Crawler ИЛИ от user-токена в `scope.go(token)`), без уже установленного `ChangeAlarm` и без одновременного `cond_stopped`/`cond_changed`, итерация останавливается с `CancellationError`, **не** с `TransactionalChangeError`. Внешний сигнал не конвертируется в `TransactionalChangeError`; simultaneous priority задаётся контрактом `raise_on_cancel`, а не этим тестом.
- *Как:* inner for-loop по двум сценариям: cancelled source token в `Crawler(..., token=SimpleToken(cancelled=True), raise_on_cancel=True)` и cancelled user token в `scope.go(SimpleToken(cancelled=True))` при `Crawler(..., raise_on_cancel=True)`. Не мутировать ФС и не останавливать scope до проверки. Ожидать `CancellationError`; assert `not isinstance(exc_info.value, TransactionalChangeError)`.

**38. `test_internal_condition_token_converts_to_transactional_change_error`**
- *Property:* Только cancellation от нашего internal ConditionToken (т.е. реальное изменение в директории) конвертируется в `TransactionalChangeError`. Это разделение обеспечивается `exc.token is condition_token`-проверкой.
- *Как:* `Crawler(..., raise_on_cancel=True)` без custom wrapper; сценарий `created` event during iteration, как в #9 runtime-subcase, но с обязательной доставкой alarm до отпускания traversal. Ожидать `TransactionalChangeError` именно из exception-path `_check_token`, а не из post-loop check; guard: callback/filter не должен исчерпать обход до alarm.

**39. `test_custom_raise_on_cancel_disambiguates_token_sources_via_cause`**
- *Property:* При `source.raise_on_cancel=CustomError` `Crawler._check_token` оборачивает CancellationError → CustomError через `raise X from original`. `go()` инспектирует `__cause__.token` только для outer exception, соответствующего configured custom `raise_on_cancel`, и выбирает результат по источнику токена: **наш condition_token → `TransactionalChangeError`** (внутренний сигнал); **user/source token → CustomError как есть** (внешний). Покрывает обе ветки disambiguation'а.
- *Как:* inner for-loop по class-form и instance-form custom exception; class-form cases обязательно включают обычный custom exception и совместимый one-arg subclass of `CancellationError`, чтобы проверить порядок wrapper-branch перед direct `isinstance(exc, CancellationError)`. Для каждой формы два сценария: (1) filesystem mutation через наш condition token (created event, как в `test_each_file_event_type_*`) → `TransactionalChangeError`; (2) cancelled source/user token → custom exception как есть, не `TransactionalChangeError`. Для instance-form каждый scenario/subcase создаёт fresh configured exception instance; внутри этого subcase проверять `exc is configured_instance`, потому что `raise` мутирует `__cause__`/traceback. Добавить false-positive guard: для class-form traversal-side `filter` поднимает ровно configured `CustomError`, для instance-form — ровно configured exception instance, но с unrelated `CancellationError` в `__cause__`, чей token не `cond_stopped`/`cond_changed`; такой exception пробрасывается как пользовательский, не конвертируется.

#### Group behavior (#40-45)

**40. `test_group_transaction_property_returns_tc_with_group_as_source`**
- *Property:* `(c1 + c2).transaction` возвращает `TransactionalCrawler`, у которого `source` — **та же** `CrawlersGroup`-инстанция (не оборачивается рекурсивно).
- *Как:* `group = Crawler('a') + Crawler('b')`. `outer = group.transaction`. `assert isinstance(outer, TransactionalCrawler)`. `assert outer.source is group`.

**41. `test_crawlers_group_apply_transactional_true_works_end_to_end`**
- *Property:* `(c1 + c2).apply(fn, transactional=True)` корректно работает: union путей из всех children, защищён через transaction, мутация во время выполнения триггерит `TransactionalChangeError`.
- *Как:* два tmp-каталога, вместе минимум два yielded path. (a) Smoke без мутации: `collected = []; (Crawler(a) + Crawler(b)).apply(collected.append, transactional=True)`. Проверить, что `collected` содержит union. (b) С мутацией: reuse captured-TC pattern из #30, но property патчить на фактическом `type(group)`, а не на child `Crawler`; callback после первого вызова через barrier ждёт `release_callback`, worker touch'ит файл в одном из child-paths, ждёт `captured_tc[0]._change_alarm.wait(timeout=2.0)`, затем `release_callback.set()`. Ожидаем `TransactionalChangeError` до callback #2; assert `len(collected) == 1`.

**42. `test_group_transaction_yields_files_from_all_children_when_static`**
- *Property:* `with group.transaction as scope` без мутаций возвращает union путей всех children с дедупликацией, как обычный `CrawlersGroup.go`.
- *Как:* overlapping children: `group = Crawler(tmp_path) + Crawler(tmp_path / 'sub')`, где `sub` входит в первый crawler. `expected = sorted(group.go())` или явно уникальный sorted-list без дублей. `with group.transaction as scope: actual = sorted(scope)` (без `set(scope)`). `assert actual == expected`.

**43. `test_group_transaction_trips_when_change_happens_in_currently_iterated_child`**
- *Property:* Изменение в **текущем** child'е (которого сейчас обходим), после исчерпания предыдущих — срывает обход.
- *Как:* group из двух Crawler (A, B). A содержит известный конечный набор, B — минимум два stable path; синхронизация через filter/barrier на первом path из B доказывает, что A уже исчерпан и сейчас итерируем B. Worker после этого сигнала трогает файл **в B**, ждёт `scope._change_alarm.wait(timeout=2.0)` и только потом отпускает traversal → следующий yield/check в B поднимает `TransactionalChangeError` с путём из B.

**44. `test_group_transaction_trips_when_change_happens_in_already_exhausted_child`**
- *Property:* Изменение в **уже исчерпанном** child'е (его обход завершён) во время итерации **следующего** — всё равно срывает обход.
- *Как:* same setup as #43, но worker трогает файл **в A** (НЕ в B!) и ожидаемый `TransactionalChangeError` содержит путь из **A**.

**45. `test_group_transaction_uses_separate_handler_per_child_with_independent_filters_non_overlapping_paths`**
- *Property:* Когда листы в группе указывают на **непересекающиеся** paths, каждый применяет свой набор фильтров изолированно: ignored event не становится first alarm ни через какой другой лист (event просто не доходит до них, потому что они в другой директории); sentinel намеренно trip'ит транзакцию.
- *Как:* `c1 = Crawler(a, exclude=['**/ignored/**'])`, `c2 = Crawler(b)` (непересекающиеся paths). Директорию `a/ignored` создать до входа в transaction. Для ignored-event ветки использовать sentinel-pattern внутри `a`: worker сначала трогает `a/ignored/x.txt` (ignored), затем `a/sentinel.txt` (allowed); message должен содержать sentinel, не ignored. Ignored delivery подтверждать watchdog-thread recording'ом или direct-handler companion, как требует общий sentinel-контракт.

### TC transaction property (#46)

**46. `test_transactional_crawler_transaction_property_idempotent_across_chain_depth`**
- *Property:* `tc.transaction` на активном TC возвращает self (идемпотентно); цепочка `.transaction.transaction…` любой глубины — то же self.
- *Как:* inner for-loop по глубинам:
  ```python
  with crawler.transaction as scope:
      for depth in [1, 3, 5]:
          obj = scope
          for _ in range(depth):
              obj = obj.transaction
          assert obj is scope
  ```

### Active `TC.apply` (#47-50)

**47. `test_tc_apply_uses_same_observer_instance_as_scope_observer`**
- *Property:* Observer, используемый при `scope.apply(fn)`, идентичен `scope._observer` через `is`-сравнение.
- *Как:* `with crawler.transaction as scope: observer_before = scope._observer; scope.apply(lambda p: None); assert scope._observer is observer_before`.

**48. `test_tc_apply_callback_runs_under_existing_observer_protection`**
- *Property:* Если во время выполнения callback'а в `scope.apply(fn)` происходит мутация, она ловится через тот же `_change_alarm`; используется существующий active observer.
- *Как:* fixture содержит минимум два yielded path. `with crawler.transaction as scope:` → callback в `apply` после первого вызова встречается с worker на barrier и ждёт `release_callback`; worker мутирует файл, ждёт `scope._change_alarm.wait(timeout=2.0)`, затем `release_callback.set()` → next yield в `apply` поднимает `TransactionalChangeError` до callback #2. Assert `len(collected) == 1`.

**49. `test_apply_rejects_non_bool_transactional_argument_and_tc_rejects_false`**
- *Property:* `transactional` — bool-флаг без truthy/falsy coercion; проверка именно `isinstance(transactional, bool)` как в скелете. `crawler.apply(..., transactional=None)` кидает `InvalidTransactionalArgumentError`; `TC.apply(..., transactional=False)` и `TC.apply(..., transactional=None)` тоже кидают `InvalidTransactionalArgumentError`.
- *Как:* для plain crawler проверить non-bool message ``The `transactional` argument must be a bool.`` при валидном callable; случай с двумя ошибками `crawler.apply(non_callable, transactional=None)` фиксирует порядок проверок из скелета и падает на существующую ошибку `PossibleCallMatcher` для non-callable/signature mismatch, не на `InvalidTransactionalArgumentError`. Для active `scope` inner for-loop по `[False, None]` плюс positional `scope.apply(fn, DefaultToken(), False)`: `False` даёт сообщение `Cannot pass transactional=False...`, non-bool — bool-message. Для inactive TC: `inactive_tc.apply(fn, transactional=False)`, `inactive_tc.apply(fn, transactional=None)` и `inactive_tc.apply(object(), transactional=False/None)` дают `InvalidTransactionalArgumentError`, раньше lifecycle/callable-check.

**50. `test_tc_apply_transactional_true_explicit_behaves_same_as_default`**
- *Property:* `scope.apply(fn, transactional=True)` поведенчески идентичен `scope.apply(fn)` (default — True на TC).
- *Как:* собрать пути дважды: `collected_default = []; scope.apply(collected_default.append)` и `collected_explicit = []; scope.apply(collected_explicit.append, transactional=True)`. `assert sorted(collected_default) == sorted(collected_explicit)`.

### Last-iteration / last-callback protection

**51. `test_mutation_during_last_element_is_caught_through_both_iteration_and_apply`**
- *Property:* Мутация на **последнем** элементе обхода всё равно ловится. #51 не параметризуется по `raise_on_cancel=True`: он фиксирует default `raise_on_cancel=False`, где post-yield check внутри `source.go()` только завершает source, а `TransactionalChangeError` поднимает post-loop check внутри `TC.go()`. Exception-path conversion при `raise_on_cancel=True` покрывается #39. Это не доказывает clean final-check само по себе. Clean `__exit__` final-check проверять отдельным subcase: обход/apply уже нормально завершился внутри `with`, затем до выхода из `with` делаем мутацию, ждём `_change_alarm.wait(timeout=2.0)`, и выход из `with` поднимает `TransactionalChangeError`. Все N callback'ов в `apply`-режиме **успевают отработать** до ошибки, поэтому `len(collected) == total`.
- *Как:* counter-based detection последнего элемента (не зависит от rglob-порядка). `total` считать как `len(list(crawler_for_count))` на отдельном crawler с теми же фильтрующими опциями, но без side-effect counter/filter (`iterdir()` нерекурсивный — даст неверный count при subdir'ах). Inner for-loop по режимам `iter` и `apply`; counter инкрементируется в filter только при `threading.current_thread() is main_thread` или в callback, на последнем элементе worker делает `touch()`, ждёт `_change_alarm.wait(timeout=2.0)` и отпускает callback/filter через `release_callback.set()`; callback/filter после запуска worker ждёт `release_callback.wait(timeout=2.0)`. Отдельный clean-exit subcase мутирует после `list(scope)` / `scope.apply(...)` вернулся, но до выхода из `with`. В `apply`-режиме assert `len(collected) == total` делать **после** `pytest.raises`, чтобы подтвердить, что последний callback успел выполниться.

### Stop during iteration

**52. `test_stop_during_iteration_aborts_workers_with_clear_error`**
- *Property:* Если главный поток выходит из `with crawler.transaction:` пока worker-потоки ещё итерируют `scope`, workers получают `TransactionStoppedDuringIterationError` на ближайшем token-check или post-loop check этого же generator'а вместо silent corruption; `next()`, который уже прошёл последний `_check_token` перед `_stop()`, может вернуть один path, поэтому тест должен продвигать iterator до ошибки. Тест не устанавливает `ChangeAlarm`: в race change+stop worker получает `TransactionStoppedDuringIterationError` только если выбран `cond_stopped`; если `_check_token` уже выбрал `cond_changed`, сохраняется `TransactionalChangeError`. Реализуется через condition_token, который ловит не только trip, но и `_active is False`.
- *Как:* worker-исключения **нельзя** ловить через `pytest.raises` из главного потока — нужен shared-container `worker_exc = []`. И **нельзя** использовать один `Barrier(2)` для всех точек синхронизации: после выхода main из `with` worker зависнет на следующем barrier. Используем `Barrier` только для первого yield и `threading.Event` для one-shot main→worker сигнала после `_stop()`. Main-side выход из `with` не должен давать `TransactionalChangeError`; после выхода проверить `not scope._change_alarm.is_alarmed()`. `main_exited.set()` выполнять в `finally` после завершения `__exit__`/`_stop()`. После `worker.join(timeout=5.0)` проверяем единственный `TransactionStoppedDuringIterationError` с точным сообщением.

**53. `test_concurrent_iteration_completes_normally_when_main_thread_waits_for_workers`**
- *Property:* Happy path: если главный поток корректно ждёт worker'ов перед выходом из `with` — никаких ошибок, обход проходит чисто.
- *Как:* `with crawler.transaction as scope:` → запустить 4 worker'а через `ThreadPoolExecutor`, каждый worker создаёт собственный iterator из `scope` и возвращает свой list paths. Сохранить futures, вызвать каждый `future.result()` до выхода из `with`, затем объединить per-worker lists в main thread. Проверить кратность: для каждого path `Counter(results)[path] == Counter(expected)[path] * 4` (каждый worker обходит всё дерево), никаких исключений.

### Nested with

**54. `test_nested_with_crawler_transaction_in_same_thread_works`**
- *Property:* Вложенные `with c.transaction as outer:` + внутри `with c.transaction as inner:` в одном потоке корректно работают: `inner` и `outer` — разные TC, у каждого свой Observer и ChangeAlarm. Ключевая проверка: два обращения к `c.transaction` на исходном `Crawler` создают два TC; `scope.transaction` на active TC возвращает self. Без мутаций выход из `inner` не останавливает `outer`.
- *Как:*
  ```python
  with c.transaction as outer:
      with c.transaction as inner:
          assert inner is not outer
          assert inner._observer is not outer._observer
          assert inner._change_alarm is not outer._change_alarm
          assert sorted(inner) == sorted(c)
      assert outer._active is True
  ```

### Apply token composition (#55-56)

**55. `test_apply_transactional_true_with_user_token_can_be_cancelled_externally`**
- *Property:* `crawler.apply(fn, token=user_token, transactional=True)` — user_token композируется с внутренним ConditionToken и может отменить итерацию извне.
- *Как:* fixture содержит минимум два yield-пути. `crawler = Crawler(tmp_path, raise_on_cancel=True)`, `user_token = SimpleToken()`. Callback после первого вызова проходит barrier и ждёт `cancelled_event.wait(timeout=2.0)`; worker после barrier делает `user_token.cancel()` и выставляет `cancelled_event`. Главный: `with pytest.raises(CancellationError): crawler.apply(callback, token=user_token, transactional=True)`. Проверить `CancellationError`, не `TransactionalChangeError`.

**56. `test_apply_without_transactional_with_user_token_works_unchanged`**
- *Property:* `crawler.apply(fn, token=user_token)` (default `transactional=False`) ведёт себя как раньше — никаких изменений из-за нашего расширения сигнатуры.
- *Как:* два subcase: `Crawler(tmp_path, raise_on_cancel=False)` с заранее cancelled `SimpleToken()` завершается тихо; `Crawler(tmp_path, raise_on_cancel=True)` с таким же token кидает `CancellationError`. Оба вызывают `crawler.apply(lambda p: None, token=user_token)` без `transactional=True`.

### Structural invariants / loader, TC property, observer and group setup

#### Loader / TC property invariants (#57-58)

**57. `test_load_watchdog_returns_same_handler_class_on_repeated_calls`**
- *Property:* `_load_watchdog` кешируется через `@functools.lru_cache(maxsize=None)`; concurrent first call не проверяется.
- *Как:* вызвать `_load_watchdog()` дважды и проверить `obs_cls_1 is obs_cls_2` и `handler_cls_1 is handler_cls_2`.

**58. `test_transactional_crawler_transaction_property_raises_when_inactive`**
- *Property:* `tc.transaction` на fresh/stopped TC поднимает `TransactionInactiveError`.
- *Как:* два subcase с тем же exact message: (1) fresh `tc = crawler.transaction`; (2) stopped `tc = crawler.transaction; tc._start(); tc._stop()`. В обоих `with pytest.raises(TransactionInactiveError, match=match('Cannot start a new transaction from an inactive TransactionalCrawler. TransactionalCrawler objects are single-use; create a new one via original_crawler.transaction.')): tc.transaction`.

#### Observer and group setup (#59-62)

**59. `test_group_transaction_uses_single_observer_with_per_leaf_handlers_sharing_one_change_alarm`**
- *Property:* (1) Outer TC создаёт **ровно один** Observer-инстанс вне зависимости от количества leaf'ов. (2) На каждый leaf — отдельный handler. (3) Все handlers ссылаются на **один и тот же** `ChangeAlarm`.
- *Как:* создать реальные директории `a`, `b`, `c` под `tmp_path`, подменить `_load_watchdog` на fully fake `Observer`/`FilteredEventHandler`, которые логируют `observer_instances`, `created_handlers`, `scheduled`. На `nested = (Crawler(a) + Crawler(b)) + Crawler(c)` внутри `with nested.transaction as scope:` проверить: один Observer, три handler'а, у всех handler'ов `h._change_alarm is scope._change_alarm`, три `schedule()` с путями `{str(a), str(b), str(c)}`; аргумент `path` в `observer.schedule(handler, path, recursive=True)` — строка. Дополнительный structural subcase: `Crawler(a) + Crawler(tmp_path / 'missing')` даёт два created handlers, но только один `schedule()` для `str(a)`. Спай через публичный `schedule()`; не читать `tc._observer.emitters`, это implementation detail watchdog.

**60. `test_three_level_nested_groups_unwrap_to_all_leaves`**
- *Property:* Произвольно глубокая вложенность `((c1 + c2) + (c3 + c4)) + c5` корректно разворачивается до пяти leaf-crawler'ов.
- *Как:* создать пять реальных директорий под `tmp_path` и построить group из 5 leaf-crawler'ов в указанной топологии. До `tc._start()` подменить `_load_watchdog` на fully fake `(ObserverSpy, HandlerSpy)`: `ObserverSpy.schedule()` логирует вызовы, `HandlerSpy` принимает те же аргументы и только сохраняет `change_alarm`/`scheduled_bases` или no-op; затем `tc = group.transaction; tc._start()` → пять schedule-вызовов; затем `tc._stop()`.

**61. `test_active_tc_inside_group_is_unwrapped_to_its_source`**
- *Property:* Если в группу вложен **уже активный** TC (`Crawler('a').transaction` внутри `with`), и эту группу обернуть в `.transaction` — внутренний `unwrap_to_crawlers()` развернёт TC до его underlying Crawler. Outer TC создаст handler для underlying Crawler.
- *Как:* До входа в `with tc_a` подменить `_load_watchdog` на fully fake `ObserverSpy`/Handler. `tc_a = Crawler(a).transaction; with tc_a: mixed = tc_a + Crawler(b); with mixed.transaction as scope: list(scope)`. Outer observer идентифицировать как `scope._observer` внутри `with mixed.transaction as scope:`, а не по порядку создания в общем логе; проверить, что его schedule вызван с путями Crawler(a) и Crawler(b), а не с tc_a как неразвёрнутым объектом.

**62. `test_overlapping_paths_in_group_use_distinct_handlers_with_independent_filters`**
- *Property:* Если два leaf-crawler'а указывают на одну и ту же базовую директорию с разными фильтрами — для каждого создаётся свой handler, применяющий свои фильтры независимо. Watchdog может внутренне дедуплицировать одинаковые OS-подписки, но это не наш контракт; наш контракт — два handler'а и два `observer.schedule(...)`. TC не делает manual fan-out: для group событие значимо, если его пропускает хотя бы один leaf-handler, которому watchdog реально доставил event, даже если `CrawlersGroup.go()` позже дедуплицировал бы такой path при выдаче. Если один event проходит несколько handlers, гарантируется `TransactionalChangeError`, а message — first-delivered handler, без source-order guarantee.
- *Как:* `c1 = Crawler(tmp_path, exclude=['**/x/**'])`, `c2 = Crawler(tmp_path, exclude=['**/y/**'])`; до входа в transaction создать директории `tmp_path/'x'` и `tmp_path/'y'`. Structural subcase с fully fake `_load_watchdog`: assert два handler'а и два `observer.schedule(...)` вызова, оба с `path == str(tmp_path)`, без дедупликации на уровне нашего кода. Runtime subcase relies on watchdog dispatching one event to every handler scheduled for the same observed path; этот invariant покрыт `test_observer_schedule_and_event_dispatch_to_handler`. Использовать общий barrier + `_change_alarm.wait(timeout=2.0)` pattern, чтобы удержать traversal активным до доставки события: `with (c1 + c2).transaction as scope:` — worker трогает `tmp_path/x/file`. Для c1 это excluded → не trip. Для c2 — не excluded → trip. Ожидаем `TransactionalChangeError`. Затем в fresh fixture/transaction зеркальный сценарий: worker трогает `tmp_path/y/file` → c1 trip, c2 не trip → `TransactionalChangeError`.

### Repr / source compatibility

**63. `test_repr_of_transactional_crawler_is_stable_across_lifecycle_states`**
- *Property:* `repr(tc)` стабильна, содержит репр source, и отображает `active` kwarg только когда состояние НЕ дефолтное (`None`). Покрывает три lifecycle-состояния: fresh (репр без `active=`), active (`active=True`), stopped (`active=False`).
- *Как:* inner for-loop по трём состояниям:
  ```python
  src = Crawler('some/path')
  fresh = src.transaction
  assert repr(fresh) == "TransactionalCrawler(Crawler('some/path'))"
  # active/stopped states use fully fake _load_watchdog; no real scheduling
  with src.transaction as scope_active:
      assert repr(scope_active) == "TransactionalCrawler(Crawler('some/path'), active=True)"
  stopped = src.transaction
  stopped._start(); stopped._stop()
  assert repr(stopped) == "TransactionalCrawler(Crawler('some/path'), active=False)"
  ```
**64. `test_external_construction_with_active_kwarg_raises_runtime_error`**
- *Property:* Прямой `TransactionalCrawler(source, active=True/False)` кидает `RuntimeError`; `TransactionalCrawler(source)` работает.
- *Как:* inner for-loop по `True`, `False`:
  ```python
  for value in [True, False]:
      with pytest.raises(RuntimeError, match=match('The `active` argument is reserved for `__repr__` only. TransactionalCrawler instances are normally created via `source.transaction`; direct construction without `active` is internal/test-only.')):
          TransactionalCrawler(crawler, active=value)
  tc = TransactionalCrawler(crawler)
  assert tc._active is None
  ```

**65. `test_unsupported_abstract_crawler_subclass_raises_type_error`**
- *Property:* Supported source iff `isinstance(source, CrawlersGroup)`, `isinstance(source, TransactionalCrawler)`, or `isinstance(source, Crawler)`; `PythonCrawler` принят только как наследник `Crawler`. Если в `TransactionalCrawler` передан любой другой `AbstractCrawler` subclass, `__enter__`/`_start()` поднимает `TypeError` при успешной/fake загрузке watchdog; тело `with` не выполняется.
- *Как:* определить минимальный custom subclass прямо внутри теста:
  ```python
  class FakeCrawler(AbstractCrawler):
      paths = ['/tmp']
      def go(self, token=DefaultToken()):
          yield from ()
  # monkeypatch TransactionalCrawler._load_watchdog to fake Observer/Handler before entering
  tc = FakeCrawler().transaction
  expected = match("Unsupported source type 'FakeCrawler' for TransactionalCrawler: expected Crawler (including PythonCrawler), CrawlersGroup, or TransactionalCrawler.")
  with pytest.raises(TypeError, match=expected):
      with tc:
          list(tc)
  ```

**66. `test_python_crawler_transaction_apply_and_extension_filter_scenarios`**
- *Property:* Для `PythonCrawler` `apply(transactional=True)` без мутаций даёт тот же набор путей, что `apply()`, а event handler применяет default `.py` extension-фильтр к filesystem events.
- *Как:* `tmp_path` с микс `.py` и `.txt`; проверить `apply(transactional=True)` без мутаций. Игнорирование `new.txt` по extension-фильтру проверять direct handler/fake event. Allowed `new.py` runtime покрыт #36.

### Error hierarchy / source invariants

#### Error hierarchy / import surface (#67)

**67. `test_all_new_transactional_errors_are_dirstree_error_subclasses`**
- *Property:* Все шесть новых исключений наследуют `DirstreeError`; `WatchdogNotInstalledError` ловится через `except ImportError`; import-surface совпадает с публичным контрактом.
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
      _ = error_class()
  assert issubclass(WatchdogNotInstalledError, ImportError)
  ```
  Затем проверить import-surface: пять user-facing ошибок импортируются из `dirstree`; `InvalidTransactionalArgumentError` импортируется только из `dirstree.errors` и отсутствует в root `dirstree`; `TransactionalCrawler` отсутствует в `dirstree` и `dirstree.crawlers.transactional`, но импортируется из `dirstree.crawlers.transactional.crawler`.

#### Cross-source invariants (#68-70)

**68. `test_filter_applies_uniformly_across_all_source_types`**
- *Property:* `filter`-callable работает одинаково для всех source-типов: событие, отфильтрованное пользовательской функцией, не trip'ит ChangeAlarm независимо от того, какой именно crawler стоит в source'е.
- *Как:* inner for-loop по 3 source-сборкам. Для каждого crawler использовать recording filter с predicate, эквивалентным `lambda p: 'ignored' not in p.name`, и sentinel-pattern: worker через barrier трогает ignored-файл, потом sentinel-файл. Для group filter задаётся на leaf'ах: `Crawler(a, filter=filter_) + Crawler(b, filter=filter_)`. Для `PythonCrawler` оба файла имеют `.py`, для остальных можно `.txt`. `assert scope._change_alarm.wait(timeout=2.0)` → проверить, что `get_message()` содержит `sentinel`, не exact ignored basename/event path. Дополнительно filter записывает watchdog-thread calls для ignored и sentinel paths под `threading.Lock` или через thread-safe queue; хранить `(seq, thread_id, path, result)`, а snapshot для assertions брать под тем же lock после `_change_alarm.wait(...)`. Assert ignored watchdog call был сделан и вернул falsey до sentinel trip, чтобы strict filter-contract не зависел только от sentinel ordering.

**69. `test_lifecycle_state_machine_works_across_all_source_types`**
- *Property:* Успешный lifecycle `_active: None → True → False` корректно работает для всех source-типов.
- *Как:* fully fake `_load_watchdog` (fake Observer/Handler). Inner for-loop по 3 source-сборкам: assert `_active is None`, затем `_start()` → `True`, `_stop()` → `False`, failed restart raises `TransactionInactiveError` and keeps terminal `False`.

**70. `test_transactional_change_error_message_format_uniform_across_source_types`**
- *Property:* Non-moved формат сообщения `TransactionalChangeError` (`Directory changed during transactional iteration: <event> at <path>`) одинаков для `Crawler` и `PythonCrawler`; moved-формат отдельно задан как `... moved from <path> to <dest>`.
- *Как:* Воспроизвести direct-handler/fake-event `created` subcase для `PythonCrawler` с `new.py`; базовый `Crawler` уже покрыт #9. Проверить один формат сообщения через source-specific expected path, не фиксированный `.txt` regexp. Real-watchdog delivery в #70 не добавлять.

### Regression / coverage гарантий

**71. `test_compile_excludes_helper_behaviorally_shared_between_traverse_and_handler`**
- *Property:* `Crawler._compile_excludes()` поведенчески идентичен в обоих code path: `_traverse` и `FilteredEventHandler` дают одинаковый результат для одного и того же exclude-pattern. Если helper разделят на независимые реализации, этот тест должен выявить расхождение.
- *Как:* `crawler = Crawler(tmp_path, exclude=['**/build/**', '*.tmp'])`. Создать `build/x.txt`, `foo.tmp`, `sentinel.txt`. (a) assert excluded paths absent from `list(crawler)` — `_traverse` применяет те же patterns. (b) В transaction touch'нуть excluded paths и затем `sentinel.txt` через worker + barrier. По sentinel-pattern: `assert scope._change_alarm.wait(timeout=2.0)` → проверить `message` содержит `sentinel.txt`, не exact ignored basenames/event paths (`x.txt`, `foo.tmp`). Добавить relative-base subcase через `monkeypatch.chdir(tmp_path.parent); crawler = Crawler(tmp_path.name, filter=record_path)` с тем же sentinel/barrier + `_change_alarm.wait(timeout=2.0)` паттерном, чтобы удержать transaction живой до доставки события. Проверить, что handler передал в filter/message path в той же relative форме, что `list(crawler)`, не resolved absolute path: `record_path` записывает watchdog-thread filter arg только для sentinel event, игнорируя main-thread traversal calls, и всегда возвращает `True` после optional recording; отдельно проверить, что filter arg равен `Path(tmp_path.name) / 'sentinel.txt'`, `ChangeAlarm` message содержит `str(Path(tmp_path.name) / 'sentinel.txt')` и не содержит `str(tmp_path)`. Точный real-watchdog `event_type` в этом subcase не фиксировать.

**72. `test_multi_path_crawler_transaction_triggers_on_change_in_any_path`**
- *Property:* `Crawler(path_a, path_b).transaction` наблюдает за **обоими** базовыми путями: изменение в любом из них срывает обход.
- *Как:* два временных каталога `a` и `b`; `crawler = Crawler(a, b)`. В loop по `changed_path in [b/'changed.txt', a/'changed.txt']`: новый `with crawler.transaction as scope`, fixture имеет stable yielded path; main-thread filter/callback блокирует traversal, worker через barrier трогает `changed_path`, ждёт `scope._change_alarm.wait(timeout=2.0)`, затем отпускает traversal; `TransactionalChangeError` ожидается на следующем продвижении iterator/`list(scope)`, не на clean `__exit__`. Exact `created at {changed_path}` проверяется direct-handler/fake-event subcase, не real watchdog. Отдельно direct-handler fake `moved` внутри одного multi-path crawler: `a/x.txt -> b/x.txt` trip'ит, а message содержит оба endpoint'а в crawler-координатах. Формат real cross-root move event не фиксировать: watchdog/OS может доставить его как `deleted`/`created`.

### Default behavior regression

**73. `test_default_apply_without_transactional_yields_identical_results_to_plain_iteration_for_all_source_types`**
- *Property:* `source.apply(fn)` (без `transactional=True`) даёт ровно тот же набор путей, что `for p in source: fn(p)`.
- *Как:* inner for-loop по `Crawler`, `PythonCrawler`, `CrawlersGroup`: собрать `collected_apply` через `source.apply(collected_apply.append)` и проверить `sorted(collected_apply) == sorted(list(source))` (сравнение без учёта порядка; дубли сохраняются).

### Поведение при завершении программы

В `tests/units/crawlers/transactional/test_crawler.py` subprocess-паттерн используют только #75-76; #74, #77 и #78 выполняются in-process. #75-76 помечаются `transactional_runtime`, потому что child process стартует real transaction/watchdog. #74/#77/#78 не помечаются `transactional_runtime`, потому что используют полностью fake `_load_watchdog`. #75-76 запускают child process через `suby.run([sys.executable, '-c', script], timeout=10.0, catch_exceptions=True)` и проверяют `returncode == 0`. При `catch_exceptions=True` timeout/kill возвращается как result object, не как исключение; используются поля `returncode`, `stdout`, `stderr`, `killed_by_token` (если timeout убил процесс, `killed_by_token` truthy). Timeout больше, чем `observer.join(timeout=5.0)`, чтобы не убивать штатный worst-case shutdown. Путь из `tmp_path` передавать в script как строку (`root = {str(tmp_path)!r}`) или через env var; внутри child использовать `Crawler(root)`, не pytest fixture `tmp_path`.

**74. `test_observer_thread_is_daemon_so_it_does_not_block_interpreter_exit`**
- *Property:* После `_start()`, `tc._observer.daemon is True`. Daemon fallback считается выполненным только этим присваиванием; внутренние watchdog emitter/internal threads не inspect/mutate, не тестируем и не обещаем для них отдельной non-blocking гарантии, даже если конкретный watchdog держит их non-daemon.
- *Как:* monkeypatch fully fake `_load_watchdog` (fake `Observer` + fake `FilteredEventHandler`), затем `with crawler.transaction as scope: assert scope._observer.daemon is True`.

**75. `test_atexit_hook_calls_stop_when_with_block_is_bypassed`**
- *Property:* Если `__exit__` пропущен, зарегистрированный `atexit`-хук вызывает `_stop_safely_at_exit` → `_stop()` при завершении интерпретатора.
- *Как:* в child script явно присвоить `original = TransactionalCrawler._stop; def spy(self): print('STOP_CALLED'); return original(self); TransactionalCrawler._stop = spy`, затем `tc.__enter__(); sys.exit(0)`. Проверить `'STOP_CALLED' in result.stdout` и `not result.killed_by_token`.

**76. `test_atexit_hook_is_actually_deregistered_after_normal_stop_via_sentinel_pattern`**
- *Property:* После нормального завершения `with`-блока (`_stop()` вызван) `atexit.unregister(_stop_safely_at_exit)` проверяет эффект удаления хука, а не только вызов unregister.
- *Как:* в child script явно присвоить spy на `TransactionalCrawler._stop_safely_at_exit`: `original = TransactionalCrawler._stop_safely_at_exit; def spy(self): print('HOOK_RAN'); return original(self)`. Затем `root = {str(tmp_path)!r}; with Crawler(root).transaction: pass`, зарегистрировать sentinel `atexit`, печатающий `SENTINEL`, и `sys.exit(0)`. Проверить `SENTINEL` есть, а `HOOK_RAN` отсутствует. Это доказывает, что hook удалён; spy на `_stop()` не проверяет этот инвариант, потому что stopped TC мог бы не вызвать `_stop()` даже при оставшемся hook.

**77. `test_stop_safely_at_exit_emits_resource_warning_when_underlying_stop_raises`**
- *Property:* `_stop_safely_at_exit` ловит `Exception` из `_stop()` и эмитит `ResourceWarning` с подробностями (не silent pass); `BaseException` не подавляется.
- *Как:* **не используем `with`** — иначе `__exit__` → `_stop()` → broken_stop поднимет повторное исключение из контекстника. Тест использует fully fake `_load_watchdog` и ручные `_start`/`_stop`-вызовы с явным `try/finally`-cleanup'ом monkeypatch'а:
  ```python
  tc = crawler.transaction
  tc._start()
  try:
      def broken_stop():
          raise RuntimeError('synthetic stop failure')
      tc._stop = broken_stop                                 # instance-level shadow
      with pytest.warns(ResourceWarning, match='synthetic stop failure'):
          tc._stop_safely_at_exit()
      # _active остался True (broken_stop never actually transitioned state)
      assert tc._active is True
  finally:
      del tc._stop                                           # restore class method
      if tc._active is True:
          tc._stop()                                         # clean shutdown
  ```
  Добавить отдельный subcase: instance-level `_stop` кидает custom `BaseException`; `tc._stop_safely_at_exit()` не подавляет его и не превращает в warning. BaseException subcase использует такой же `try/finally` cleanup: удалить instance-level `_stop`, затем при `tc._active is True` вызвать настоящий `tc._stop()`.
  Добавить отдельный normal `_stop()` subcase с fake Observer, у которого `stop()` кидает `RuntimeError('synthetic observer stop failure')` до `_active=False`: `tc._start(); with pytest.raises(RuntimeError, match='synthetic observer stop failure'): tc._stop()`. Проверить exact state: `tc._active is True`, spy на `atexit.unregister` call count == 0; приватное состояние `atexit` не инспектировать. Cleanup в `finally`: заменить fake observer `stop` на успешный no-op и вызвать настоящий `tc._stop()` если `tc._active is True`.

**78. `test_stop_emits_resource_warning_when_observer_join_times_out`**
- *Property:* Существующий `warnings.warn(ResourceWarning)` в `_stop()` реально срабатывает, когда `observer.join(timeout=5.0)` не дождался смерти потока.
- *Как:* Через `_load_watchdog` с fake Observer (`join()` no-op, `stop()` не меняет alive-state или `is_alive()` всё равно returns `True` после `stop()`/`join()`), чтобы не оставить живой watchdog thread. **Не используем `with`** — `__exit__` вызовет `_stop()` повторно после нашего ручного `_stop()`, что даст `TransactionInactiveError`:
  ```python
  monkeypatch.setattr(TransactionalCrawler, '_load_watchdog', staticmethod(fake_load_watchdog_with_stuck_observer))
  tc = crawler.transaction
  tc._start()
  with pytest.warns(ResourceWarning, match=match('TransactionalCrawler observer thread did not terminate within 5s of stop(). This is a watchdog/OS-layer bug; the thread is being left as a daemon.')):
      tc._stop()
  # tc._active is now False; no further cleanup needed.
  ```

### Python/dependency invariant tests

#### `tests/python/interpreter/test_atexit.py`

Все три теста используют `suby.run(..., timeout=10.0, catch_exceptions=True)` и проверяют stdout/returncode. Все sentinel prints в subprocess-скриптах используют `print(..., flush=True)` или явный `sys.stdout.flush()`, включая negative sentinel `SHOULD_NOT_FIRE`, иначе `os._exit` может скрыть выполненный callback в буфере stdout.

**`test_atexit_fires_callbacks_on_sys_exit`**
- *Property:* `atexit`-callbacks вызываются при `sys.exit(0)` (нормальный выход через `SystemExit`).
- *Docstring (mandatory, подробный):* (а) на какую часть реализации опирается инвариант: `_stop_safely_at_exit`, зарегистрированный в `TransactionalCrawler._start()` через `atexit.register`, должен вызваться при любом нормальном завершении интерпретатора (включая `sys.exit()`); (б) какое поведение сломается, если инвариант перестанет работать: graceful shutdown сломается — при обходе `with`-блока и вызове `sys.exit()` `_stop()` не вызовется; если такой cleanup должен был бы упасть, diagnostic `ResourceWarning` тоже не появится.
- *Как:* subprocess регистрирует sentinel-handler, делает `sys.exit(0)`, parent проверяет `'ATEXIT_FIRED' in result.stdout`.

**`test_atexit_does_not_fire_callbacks_on_os_exit`**
- *Property:* `atexit`-callbacks **НЕ** вызываются при `os._exit(0)`. Это контр-инвариант: cleanup активной транзакции при `os._exit` не поддерживается.
- *Как:* subprocess регистрирует handler, печатающий `SHOULD_NOT_FIRE`, делает `os._exit(0)`, parent проверяет отсутствие sentinel в stdout.

**`test_atexit_handler_can_call_unregister_on_itself_during_atexit_iteration`**
- *Property:* `atexit.unregister(bound_method)` вызванный **внутри** `atexit`-handler'а (когда CPython итерирует callback-список) не corrupt'ит чейн. Это соответствует production path `_stop_safely_at_exit()` → `_stop()` → `atexit.unregister(self._stop_safely_at_exit)`. Если CPython однажды поменяет внутреннюю структуру списка callbacks или bound-method equality для unregister, путь сломается.
- *Как:* subprocess создаёт объект с методом `self_unregistering(self)`, который делает `atexit.unregister(self.self_unregistering)` и печатает `SELF_UNREG_RAN`; регистрирует сначала `sentinel()` (`SENTINEL`), затем `obj.self_unregistering`. Из-за LIFO parent проверяет обе строки в stdout и порядок `result.stdout.index('SELF_UNREG_RAN') < result.stdout.index('SENTINEL')`.

#### `tests/python/interpreter/test_atomicity.py`

**`test_plain_attribute_write_is_visible_to_concurrent_readers_within_short_time`**
- *Property:* Запись plain Python-attribute одним потоком становится видимой другим потокам в разумный срок без lock'а на стороне читателей. На этом стоит `ConditionToken(lambda: self._active is not True)` в `TransactionalCrawler.go()`.
- *Как:* 8 reader-потоков через `Barrier` одновременно читают `holder.flag` до deadline `2.0`; main меняет `holder.flag = False`; assert `all(saw_change)`. При падении этот invariant-test считается blocking; заменять чтение `_active` в `cond_stopped` на `threading.Event`-based сигнал запрещено без отдельного design change.

#### `tests/python/dependencies/test_cantok.py`

**`test_composite_token_cancellation_exposes_subtoken_identity_in_exc_token`**
- *Property:* `cantok.CancellationError` от composite token (`token_a + token_b`) несёт в `exc.token` identity именно сработавшего leaf-токена; если сработали два leaf'а, выбирается первый слева. На этом стоят `exc.token is cond_stopped/cond_changed` и приоритет explicit user-token в `TransactionalCrawler.go()` after source entry. Уже alarmed/stopped до первого `source.go()` path не зависит от этого invariant: его покрывают explicit pre-source checks.
- *Как:* (1) `flag = False`; `fired = ConditionToken(lambda: flag)`, `not_fired = ConditionToken(lambda: False)`, `composite = SimpleToken() + fired + not_fired`; сначала `composite.check()` не должен raise, затем выставить `flag = True`, снова вызвать `composite.check()` и assert `exc_info.value.token is fired`. Это обязательно проверяет identity для `ConditionToken` на переходе False→True, потому что на нём держится `exc.token is cond_stopped/cond_changed` после входа в `source.go()`. (2) `first = SimpleToken(); second = SimpleToken(); composite = first + second; first.cancel(); second.cancel(); composite.check()` → `exc.token is first`. Этот left-to-right identity guarantee проверяется только для composite, созданного до cancel leaf-токенов. При падении users увидят raw `CancellationError` вместо доменных ошибок или изменится приоритет user-token.

#### `tests/python/dependencies/test_watchdog.py`

Тесты инвариантов `watchdog`-API, на которые опирается наша реализация или тестовая синхронизация. Весь файл помечен `@pytest.mark.transactional_runtime`, потому что каждый тест импортирует watchdog внутри своего тела или fixture. Failure любого из этих тестов означает изменение watchdog-контракта и требует пересмотра реализации или sentinel-тестов.

**`test_event_type_strings_are_exact_literals`**
- *Property:* `watchdog.events.FileSystemEvent.event_type` для разных типов событий принимает **строго** значения `'created'`, `'deleted'`, `'modified'`, `'moved'`. На эти строки прямо опирается формирование `TransactionalChangeError.message` в `ChangeAlarm.set_first`.
- *Что сломается:* если watchdog поменяет нейминг, exception messages станут wrong и exact-message tests начнут падать.
- *Как:* импортировать `FileCreatedEvent`, `FileDeletedEvent`, `FileModifiedEvent`, `FileMovedEvent`. Создать инстансы напрямую через constructors этих классов: created/deleted/modified получают один path, moved получает `src_path` и `dest_path`. Проверить `event.event_type == 'created'` и т.д. для каждого.

**`test_observer_schedule_and_event_dispatch_to_handler`**
- *Property:* `watchdog.observers.Observer().schedule(handler, path, recursive=True)` принимает `FileSystemEventHandler` subclass и подписывает handler на события в `path`. После `observer.start()`, мутации в `path` доставляются как вызовы `handler.on_any_event(event)` (с правильно установленными `event.src_path`, `event.event_type` и т.д.), включая fan-out одного и того же supported event во все handlers, scheduled на тот же path.
- *Что сломается:* основной механизм нашей реализации — handler не получит событий, transactional перестанет работать.
- *Как:* `tmp_path`. Создать два кастомных handler'а как subclasses of `FileSystemEventHandler`, оба scheduled на `str(tmp_path)`. Каждый handler имеет свой `received` и `event_arrived`; на `on_any_event(event)` добавляет tuple `(event.event_type, event.src_path, getattr(event, 'dest_path', None))` для supported file-event по `str(tmp_path/'foo.txt')` и выставляет свой `event_arrived` после добавления. `observer.schedule(handler1, str(tmp_path), recursive=True); observer.schedule(handler2, str(tmp_path), recursive=True); observer.start(); (tmp_path/'foo.txt').touch(); assert event_arrived_1.wait(timeout=2.0); assert event_arrived_2.wait(timeout=2.0)`. В cleanup/finally: `observer.stop(); observer.join(timeout=5.0); assert not observer.is_alive()`. Проверить, что пересечение `received1 & received2` содержит tuple с `src_path == str(tmp_path/'foo.txt')` и `event_type` из supported set.

**`test_observer_single_dispatcher_processes_events_sequentially`**
- *Property:* У одного `Observer` один dispatcher-поток; события из одного `emitter` доставляются в порядке последовательных мутаций и обрабатываются handler'ом **последовательно** (не параллельно). Это тестовый инвариант для sentinel-pattern tests выше; production `ChangeAlarm` остаётся thread-safe и не требует single-dispatcher для first-wins.
- *Что сломается:* если watchdog в будущем перейдёт на multi-threaded dispatch — sentinel-pattern сломается: ignored и sentinel могут быть обработаны параллельно, и first-wins недетерминирован; это требует пересмотра sentinel-тестов, а не добавления runtime-сериализации в TC.
- *Как:* handler для первого supported file-event по каждому `seq_*.txt` под lock'ом инкрементит `active_count`; если `active_count > 1`, записывает `handler_exc = AssertionError(...)`, а не делает голый `assert` внутри watchdog thread. Directory events игнорируются; duplicate event для уже записанного имени не добавляется в `received` повторно. Имя добавляется в `received` под lock'ом. `block_event = threading.Event()` intentionally never set; после increment/check lock отпускается, и каждый такой `seq_*` event делает `block_event.wait(timeout=0.05)` вне lock как короткое blocking window для обнаружения parallel handler execution. Каждый обработанный `seq_*` event в `finally` под lock'ом декрементит `active_count`; для `seq_09.txt` handler выставляет `last_event_seen` только после decrement и только если `active_count == 0`. Worker создаёт `seq_00.txt` … `seq_09.txt` по порядку; parent делает `assert last_event_seen.wait(timeout=2.0)`, затем под тем же lock копирует `handler_exc` и `received` для assertions. Две отдельные assertions: `handler_exc is None` доказывает отсутствие overlap, а `received == [f'seq_{i:02}.txt' for i in range(10)]` доказывает ordering; если до `seq_09` не пришёл любой предыдущий `seq_*`, это должно считаться failure инварианта watchdog ordering.

**`test_observer_start_stop_join_lifecycle`**
- *Property:* чистый lifecycle Observer завершается после `stop()`/`join(timeout=5.0)`.
- *Что сломается:* если watchdog изменит lifecycle-API или станет вешаться на `join()` без timeout — наш `_stop()` будет блокировать навечно.
- *Как:* создать Observer, schedule на tmp_path, start, stop, `observer.join(timeout=5.0)` — `assert not observer.is_alive()`.

Для setup-failure unit tests, где `_observer` падает после успешных `Observer()` и `observer.daemon = True`, внутри schedule-cleanup `try` fake Observer обязан поддерживать `stop()`/`join()` до `start()`; failures на `Observer()` или daemon assignment cleanup не запускают. Реальные watchdog `stop()`/`join()` в этом пути считаются best-effort cleanup, и любые их `Exception` превращаются в `ResourceWarning` по контракту выше.

### Stress-сьют (не default pytest; обязательный отдельный прогон)

Fixture `cpu_pressure` поднимает `multiprocessing.cpu_count()` `multiprocessing.Process`-воркеров, каждый крутит `hashlib.sha256(b'x' * 4096).digest()` в петле (real CPU-bound workload, не `while True: pass`). Target CPU worker — top-level picklable helper в `tests/conftest.py` для `spawn` на macOS/Windows. `stop` — `multiprocessing.Event`, переданный каждому process target. После теста: `stop.set()`, для каждого процесса `join(timeout=2.0)`, если всё ещё alive — `terminate()` и повторный `join(timeout=2.0)`. Для повторов stress-сценариев использовать `STRESS_REPEATS = 20`. Переиспользование core-сценариев делать без module-level helper: через локальную вложенную функцию внутри теста, fixture в `conftest.py` или осознанное дублирование кода сценария внутри stress-теста.

Тесты ниже помечаются обоими маркерами: `@pytest.mark.stress` и `@pytest.mark.transactional_runtime`.

- `test_creation_during_iteration_under_cpu_pressure_still_trips` — переиспользует `created`-сценарий из `test_each_file_event_type_during_iteration_raises_with_correct_message` + `cpu_pressure`.
- `test_two_threads_under_cpu_pressure_remain_isolated` — переиспользует `test_two_threads_each_with_own_transaction_are_fully_isolated` с `cpu_pressure`-параметром.
- `test_apply_transactional_under_cpu_pressure_still_trips_correctly` — переиспользует `test_apply_transactional_true_raises_on_change_and_stops_calling_function` с `cpu_pressure`-параметром.

## Инфраструктура / CI

- `pyproject.toml` — оставить базовый `requires-python >=3.8`; в `[project.optional-dependencies]` добавить extra `transactional = ['watchdog==6.0.0; python_version >= "3.9"']` и документировать, что на Python 3.8 имя extra installable, но из-за environment marker не ставит watchdog, поэтому transactional mode недоступен. `_load_watchdog()` явно проверяет `sys.version_info < (3, 9)` до import watchdog и поднимает `WatchdogNotInstalledError` с текстом про Python >=3.9. В `[tool.pytest.ini_options]` добавить markers `stress`, `transactional_runtime` к существующим marker'ам (не заменяя `mypy_testing`) и `addopts = ["-m", "not stress"]`; смысл `transactional_runtime`, запрет module-level watchdog imports и исключения для dependency/version-gate тестов заданы в `Test infrastructure` выше, CI не вводит второй контракт. Для обычного `pytest`/coverage job effective marker expression = `not stress`; plain `pytest` поддержан только в transactional-capable env. Для compatibility/base env, включая Python 3.8, ожидаемый запуск — `pytest -o addopts='' -m "not stress and not transactional_runtime"`; это исключение не переносить в default addopts и не заменять `skipif`. Все watchdog imports в production/tests должны проходить mypy без установленного extra в lint job: локальные строки import'а получают точечный `# type: ignore[import-not-found,import-untyped]` и, где ruff поднимает local-import warning, `# noqa: PLC0415`; annotations не ссылаются напрямую на watchdog-типы, а `class FilteredEventHandler(FileSystemEventHandler)` получает точечный `# type: ignore[misc]` против subclassing `Any`.
- `.github/workflows/tests_and_coverage.yml` — разделить текущий `build` на два отдельных job'а: `compatibility` и `transactional_coverage`. `compatibility` использует ровно matrix: OS `[macos-latest, ubuntu-latest, windows-latest]`, Python `["3.8", "3.9", "3.10", "3.11", "3.12", "3.13", "3.14", "3.14t", "3.15.0-alpha.1"]`; install steps: `pip install .`, затем `pip install -r requirements_dev.txt`; затем `pytest -o addopts='' -m "not stress and not transactional_runtime"` без coverage/Coveralls. `transactional_coverage` использует **точно** следующую authoritative matrix, не derive-from-current: OS `[macos-latest, ubuntu-latest, windows-latest]`, Python `["3.9", "3.10", "3.11", "3.12", "3.13", "3.14", "3.14t", "3.15.0-alpha.1"]`, включая prerelease/free-threaded. Matrix фиксирована; `allow-prereleases`, matrix rewrites или excludes являются отдельным documented CI-policy change. Install steps для `transactional_coverage`: `pip install -e '.[transactional]'`, затем `pip install -r requirements_dev.txt`. Workflow выполняет non-branch coverage command из Verification п.4, затем `coverage xml`, затем Coveralls upload на каждом Linux matrix entry, затем branch coverage command из Verification п.4 как отдельный gate; Coveralls намеренно получает XML именно от non-branch coverage run. "Ровно" относится к самим `coverage run && coverage report` командам из Verification. Python 3.8 не должен пытаться установить transactional extra; если `watchdog` не ставится или transactional runtime не работает на любой non-3.8 matrix entry, job остаётся failing без documented skip/exclude с причиной.
- `.github/workflows/lint.yml` — сохраняет текущую matrix и install steps: `pip install -r requirements_dev.txt`, затем `pip install .`, без `.[transactional]`; optional-import policy — как в `pyproject.toml` bullet выше.
- `requirements_dev.txt` — добавить test-only `locklib==0.0.23` и `suby==0.0.12`; `watchdog` сюда не добавлять, он приходит из `.[transactional]`.

## Документация

- `README.md` — расширить существующий раздел «Transactionality»: старый текст про `freeze=True` не оставлять как рекомендацию консистентности, а описать как legacy snapshot behavior; transactional mode описать отдельно как abort-on-change. Описать: `with crawler.transaction as scope:` и `apply(transactional=True)`, extras (`pip install dirstree[transactional]`), `TransactionalChangeError`, требование Python >=3.9 для watchdog runtime; на Python 3.8 extra может установиться без watchdog, а использование transactional mode даст `WatchdogNotInstalledError`. Явно отметить: (a) best-effort гарантия (платформенные latency-окна), (b) ресурсная стоимость (фоновый Observer-поток + ОС-подписка на каждую транзакцию), (c) multi-threading: безопасна по TC-owned ресурсам одновременная итерация из нескольких потоков, когда каждый worker создаёт свой iterator из `scope`; `source`, `source.token` и custom source/filter state не изолируются; главный поток обязан дождаться всех worker'ов, которые используют `scope` или могут вызвать `iter(scope)`/`scope.go()`/`scope.apply()`, до выхода из `with`; `TransactionStoppedDuringIterationError` получают только итераторы, уже вошедшие в активный `TC.go()` и отменённые через `cond_stopped`, а новые обращения к stopped scope дают `TransactionInactiveError` (`iter(scope)`/`scope.go()` как generator API кидают его при первом продвижении через `next()`/`list(...)`; `scope.apply()` — сразу), (d) composition с user-token: user-token сохраняет обычную семантику `raise_on_cancel`; внутренний change-token → `TransactionalChangeError`; internal stop-token → `TransactionStoppedDuringIterationError`, когда именно он стал источником cancellation (приоритеты одновременной отмены см. контракт `raise_on_cancel`), (e) shutdown: graceful cleanup через `__exit__`/`atexit`; daemon fallback означает именно `Observer.daemon = True`, гарантий по внутренним watchdog emitter threads не даём, и `_stop()` не гарантируется при обходе `atexit` вроде `os._exit`; ручной cleanup не требуется, (f) в transactional mode пользовательский `filter` может вызываться параллельно из нескольких пользовательских worker-потоков, одновременно итерирующих `scope`, и watchdog thread; сериализации нет, thread-safety filter'а на пользователе, (g) watchdog подписывается только на существующие directory base paths.
- Docstring `Crawler` — параграф о `transaction` и `apply(transactional=True)`.
- Docstring `TransactionalCrawler` — кратко покрывает abort-on-change, best-effort latency, отсутствие parent-watch/drain, watcher только для existing directory bases, concurrent `filter` calls и требование Python >=3.9/watchdog extra.

## Verification

Эти шаги нужно выполнить **последовательно в конце имплементации** как verification gate:

1. Проверить копию плана из раздела «Структура файлов»: `docs/plans/transactions.md` существует и byte-for-byte совпадает с `docs/assets/plans/1 - transactional crawling.md`.
2. На Python >=3.9 установить test env с optional extra: `python -m pip install -e '.[transactional]'` (или эквивалентный проектный install command). На Python 3.8 extra name может установиться без watchdog из-за marker, но transactional mode всё равно недоступен и должен падать в `_load_watchdog()`; transactional extra/watchdog runtime tests на 3.8 не запускаются: это обеспечивается compatibility job командой `pytest -o addopts='' -m "not stress and not transactional_runtime"`, без `skipif` в самих runtime-тестах.
3. Полный transactional-capable suite на Python >=3.9: `pytest` — должен проходить, включая `tests/units/crawlers/transactional/test_crawler.py` и `tests/python/dependencies/test_watchdog.py`. Для обычного `pytest`/coverage job effective marker expression ровно `not stress` через `addopts = ["-m", "not stress"]`; plain `pytest` в Python 3.8/base env не является ожидаемым запуском.
4. 100% coverage gate из CI workflow:
   ```bash
   coverage run --source=dirstree --omit="*tests*" -m pytest --cache-clear --assert=plain && coverage report -m --fail-under=100
   coverage run --branch --source=dirstree --omit="*tests*" -m pytest --cache-clear --assert=plain && coverage report -m --fail-under=100
   ```
   Ветка «watchdog отсутствует» покрывается тестом #33 через `sys.modules[name] = None` + `TransactionalCrawler._load_watchdog.cache_clear()`, чтобы пройти реальный `try/except ImportError`.
5. Lint/type выполнять в lint-equivalent base env без transactional extra: `pip install -r requirements_dev.txt`, затем `pip install .`, без `pip install -e '.[transactional]'`; если предыдущие шаги выполнялись в env с extra, создать свежий env для этого gate:
   ```bash
   ruff check dirstree
   ruff check tests
   mypy --strict dirstree
   mypy tests --exclude typing
   ```
6. **Stress-сьют — обязательный отдельный шаг**. Выполняется **отдельно от полного suite** (`pytest` в дефолтной конфигурации исключает stress через `addopts = ["-m", "not stress"]`) и в transactional-capable env с `pip install -e '.[transactional]'`; если lint/type выполнялись в отдельном base env, перед stress вернуться в env с extra или создать новый. Прогон нужен, чтобы убедиться, что race-сценарии стабильны под CPU pressure:
   ```bash
   pytest -o addopts='' -m stress tests/units/crawlers/transactional/test_crawler.py
   ```
   Effective marker expression этого запуска должен быть `stress`, а не `not stress`.
   Любой timeout или отсутствие `ChangeError` в stress-прогоне блокирует завершение до разбора причины.
7. Smoke вручную:
   ```python
   from dirstree import Crawler
   c = Crawler('.', exclude=['.git/**', '__pycache__/**'])
   with c.transaction as scope:
       for p in scope:
           print(p)
   # Пока with ещё активен, в соседнем терминале — `touch foo.txt` → TransactionalChangeError.
   ```
   Для smoke использовать достаточно большое дерево или временно замедлить `print/process`, чтобы `touch` точно произошёл до выхода из `with`; если обход закончился раньше, отсутствие `TransactionalChangeError` в этом случае не является failure-сигналом.

## Приложение: `issue_self.md`

Создать в корне проекта со следующим содержимым (формат GitHub issue, английский):

```markdown
# FileNotFoundError leaks from `rglob` during concurrent file deletion

## Description

`Crawler._traverse()` iterates the filesystem via `pathlib.Path.rglob('*')`. `rglob` recursively scans directories while the tree may be changing. The current implementation then applies `is_file()` / `is_dir()` / filter checks to yielded paths.

If a directory is removed while `rglob` is descending into it, CPython's recursive scan can raise **`FileNotFoundError`** from the generator itself. Depending on Python version and operation, later stat-dependent checks can also be part of the race surface. This exception propagates out of `_traverse()` and breaks iteration with an error unrelated to the user's API.

## Reproduction

Create many nested directories/files in a temp dir and race `Crawler(root)` iteration against a background `Thread(target=deleter)` that removes nested directories in parallel. Under concurrent deletion, `Path.rglob('*')` may raise `FileNotFoundError` for directories that vanished between listing and recursive descent.

## Impact

- Affects all `Crawler` uses, not only the transactional mode.
- Becomes more visible in `TransactionalCrawler` because users of that mode explicitly expect the directory to be mutated during iteration.
- In transactional mode, the user expects `TransactionalChangeError`. Instead they may receive a bare `FileNotFoundError` (raised before the transaction's `ConditionToken` gets a chance to trip).

## Suggested fix

1. **Preferred: inside `Crawler._traverse`** — handle `FileNotFoundError` around `rglob` iteration and any subsequent stat-dependent checks. Silently skip vanished entries/subtrees. This fixes the root cause for all `Crawler` callers.
2. **Inside `TransactionalCrawler.go`** — additionally catch `FileNotFoundError`; if `self._change_alarm.is_alarmed()`, raise `TransactionalChangeError` (since the missing file is the change the transaction was detecting). Otherwise re-raise.

## Scope

Pre-existing `Crawler` traversal bug; address in `Crawler` itself.
```
