import os
from functools import partial
from pathlib import Path
from typing import Type, Union

import pytest
from cantok import ConditionToken, DefaultToken, SimpleToken
from full_match import match
from sigmatch.errors import SignatureMismatchError

from dirstree import Crawler, PythonCrawler


def custom_filter(path: Path) -> bool:  # noqa: ARG001
    return True


def test_crawl_test_directory_with_default_extensions(
    crawl_directory_path: Union[str, Path],
):
    crawler = Crawler(crawl_directory_path)

    expected_paths = [
        os.path.join('tests', 'test_files', 'walk_it', '__init__.py'),
        os.path.join('tests', 'test_files', 'walk_it', 'simple_code.py'),
        os.path.join(
            'tests', 'test_files', 'walk_it', 'nested_folder', 'non_python_file.txt',
        ),
        os.path.join(
            'tests', 'test_files', 'walk_it', 'nested_folder', 'python_file.py',
        ),
        os.path.join('tests', 'test_files', 'walk_it', 'nested_folder', '__init__.py'),
    ]
    real_paths = [str(x) for x in crawler]

    expected_paths.sort()
    real_paths.sort()

    assert real_paths == expected_paths


def test_crawl_test_directory_with_txt_extension(
    crawl_directory_path: Union[str, Path],
):
    crawler = Crawler(crawl_directory_path, extensions=['.txt'])

    assert [str(x) for x in crawler] == [
        os.path.join(
            'tests', 'test_files', 'walk_it', 'nested_folder', 'non_python_file.txt',
        ),
    ]


def test_crawl_test_directory_with_py_extension(crawl_directory_path: Union[str, Path]):
    crawler = Crawler(crawl_directory_path, extensions=['.py'])

    expected_paths = [
        os.path.join('tests', 'test_files', 'walk_it', '__init__.py'),
        os.path.join('tests', 'test_files', 'walk_it', 'simple_code.py'),
        os.path.join(
            'tests', 'test_files', 'walk_it', 'nested_folder', 'python_file.py',
        ),
        os.path.join('tests', 'test_files', 'walk_it', 'nested_folder', '__init__.py'),
    ]
    real_paths = [str(x) for x in crawler]

    expected_paths.sort()
    real_paths.sort()

    assert real_paths == expected_paths


def test_crawl_test_directory_with_exclude_with_py_extension(
    crawl_directory_path: Union[str, Path],
):
    crawler = Crawler(crawl_directory_path, exclude=['__init__.py'], extensions=['.py'])

    assert [str(x) for x in crawler] == [
        os.path.join('tests', 'test_files', 'walk_it', 'simple_code.py'),
        os.path.join(
            'tests', 'test_files', 'walk_it', 'nested_folder', 'python_file.py',
        ),
    ]


def test_crawl_test_directory_with_exclude_patterns_without_extensions(
    crawl_directory_path: Union[str, Path],
):
    crawler = Crawler(crawl_directory_path, exclude=['__init__.py'])

    expected_paths = [
        os.path.join('tests', 'test_files', 'walk_it', 'simple_code.py'),
        os.path.join(
            'tests', 'test_files', 'walk_it', 'nested_folder', 'non_python_file.txt',
        ),
        os.path.join(
            'tests', 'test_files', 'walk_it', 'nested_folder', 'python_file.py',
        ),
    ]
    real_paths = [str(x) for x in crawler]

    expected_paths.sort()
    real_paths.sort()

    assert real_paths == expected_paths


def test_crawl_test_directory_with_exclude_patterns_and_extensions(
    crawl_directory_path: Union[str, Path],
):
    crawler = Crawler(
        crawl_directory_path, extensions=['.txt'], exclude=['__init__.py'],
    )

    assert [str(x) for x in crawler] == [
        os.path.join(
            'tests', 'test_files', 'walk_it', 'nested_folder', 'non_python_file.txt',
        ),
    ]


@pytest.mark.parametrize(
    ('crawler', 'expected_repr'),
    [
        (Crawler('.'), "Crawler('.')"),
        (Crawler('usr/bin'), "Crawler('usr/bin')"),
        (Crawler('.', extensions=['.py']), "Crawler('.', extensions=['.py'])"),
        (Crawler('.', exclude=['*.py'], extensions=['.py']), "Crawler('.', extensions=['.py'], exclude=['*.py'])"),
        (Crawler('.', exclude=['*.py']), "Crawler('.', exclude=['*.py'])"),
        (Crawler('.', filter=custom_filter), "Crawler('.', filter=custom_filter)"),
        (Crawler('.', filter=lambda x: True), "Crawler('.', filter=lambda x: True)"),  # noqa: ARG005
        (Crawler('.', token=ConditionToken(lambda: True)), "Crawler('.', token=ConditionToken(λ))"),
        (Crawler('../dirstree') + Crawler('../cantok'), "CrawlersGroup([Crawler('../dirstree'), Crawler('../cantok')])"),
        (Crawler('../dirstree') + PythonCrawler('../cantok'), "CrawlersGroup([Crawler('../dirstree'), PythonCrawler('../cantok')])"),
    ],
)
def test_repr(crawler: Crawler, expected_repr: str):
    assert repr(crawler) == expected_repr


@pytest.mark.parametrize(
    'factory',
    [
        Crawler,
        PythonCrawler,
    ],
)
def test_iter(factory: Type[Crawler]):
    crawler = factory('.')

    assert list(crawler) == list(crawler.go())


@pytest.mark.parametrize(
    'factory',
    [
        Crawler,
        PythonCrawler,
    ],
)
def test_crawl_repeat(factory: Type[Crawler]):
    crawler = factory('.')

    assert list(crawler) == list(crawler)


@pytest.mark.parametrize(
    'factory',
    [
        Crawler,
        PythonCrawler,
    ],
)
def test_filter_first(factory: Type[Crawler]):
    index = 0

    def empty_filter(path) -> bool:  # noqa: ARG001
        nonlocal index

        result = index != 0

        index += 1

        return result

    assert list(factory('.'))[1:] == list(factory('.', filter=empty_filter))


@pytest.mark.parametrize(
    'factory',
    [
        Crawler,
        PythonCrawler,
    ],
)
def test_argument_of_filter_is_path_object(crawl_directory_path: Union[str, Path], factory: Type[Crawler]):
    collector = []

    def empty_filter(path):
        collector.append(path)
        return True

    crawler = factory(crawl_directory_path, filter=empty_filter)

    assert list(crawler) == collector


@pytest.mark.parametrize(
    'factory',
    [
        Crawler,
        PythonCrawler,
    ],
)
@pytest.mark.parametrize(
    'n',
    [
        0,
        1,
        2,
        3,
    ],
)
def test_cancel_after_n_iteranions(crawl_directory_path: Union[str, Path], n: int, factory: Type[Crawler]):
    index = 0

    def empty_filter(path: Path) -> bool:  # noqa: ARG001
        nonlocal index
        index += 1
        return True

    def condition() -> bool:
        return index == n

    token = ConditionToken(condition)

    crawler = factory(crawl_directory_path, token=token, filter=empty_filter)

    assert list(factory(crawl_directory_path))[:n] == list(crawler)


@pytest.mark.parametrize(
    'factory',
    [
        Crawler,
        PythonCrawler,
    ],
)
def test_cancelled_token(crawl_directory_path: Union[str, Path], factory: Type[Crawler]):
    assert list(factory(crawl_directory_path, token=SimpleToken(cancelled=True))) == []


@pytest.mark.parametrize(
    'factory',
    [
        Crawler,
        PythonCrawler,
    ],
)
def test_default_token(crawl_directory_path: Union[str, Path], factory: Type[Crawler]):
    assert list(factory(crawl_directory_path, token=DefaultToken())) == list(
        factory(crawl_directory_path),
    )


def test_pass_not_starting_with_dot_extension(crawl_directory_path: Union[str, Path]):
    with pytest.raises(
        ValueError,
        match=match(  # type: ignore[operator]
            'The line with the file extension must start with a dot. You have passed: "txt".',
        ),
    ):
        Crawler(crawl_directory_path, extensions=['txt'])


def test_deduplication_with_sum_of_crawlers(crawl_directory_path: Union[str, Path]):
    assert list(Crawler(crawl_directory_path) + Crawler(crawl_directory_path)) == list(Crawler(crawl_directory_path))


def test_deduplication_with_sum_of_crawlers_and_group(crawl_directory_path: Union[str, Path]):
    assert list(Crawler(crawl_directory_path) + (Crawler(crawl_directory_path) + Crawler(crawl_directory_path))) == list(Crawler(crawl_directory_path))


def test_sum_of_crawlers(crawl_directory_path: Union[str, Path]):
    first_crawler = Crawler(crawl_directory_path, extensions=['.py'])
    second_crawler = Crawler(crawl_directory_path, extensions=['.txt'])

    supercrawler = first_crawler + second_crawler

    supercrawlers_result = list(supercrawler)
    simplecrawlers_result = list(Crawler(crawl_directory_path))

    supercrawlers_result.sort()
    simplecrawlers_result.sort()

    assert supercrawlers_result == simplecrawlers_result


def test_sum_usual_crawler_and_python_crawler():
    first_crawler = Crawler('.', extensions=['.py'])
    second_crawler = Crawler('.', filter = lambda x: x.suffix != '.py')

    sum_result = list(first_crawler + second_crawler)
    default_result = list(Crawler('.'))

    sum_result.sort()
    default_result.sort()

    assert sum_result == default_result


def test_try_to_sum_with_not_crawler():
    with pytest.raises(TypeError, match=match("Cannot add Crawler and int.")):
        Crawler('.') + 1

    with pytest.raises(TypeError, match=match("Cannot add Crawler and str.")):
        Crawler('.') + 'kek'


def test_crawl_two_folders(crawl_directory_path: Union[str, Path], second_crawl_directory_path: Union[str, Path]):
    assert list(Crawler(crawl_directory_path, second_crawl_directory_path)) == list(Crawler(crawl_directory_path)) + list(Crawler(second_crawl_directory_path))


def test_crawl_without_path():
    assert list(Crawler()) == []


def test_check_filter_signature():
    with pytest.raises(SignatureMismatchError, match=match('The signature of the callable object does not match the expected one.')):
        Crawler(filter=lambda: None)


def test_apply_calls_function_once_per_file(crawl_directory_path: Union[str, Path]):
    seen: list = []
    Crawler(crawl_directory_path).apply(seen.append)
    assert len(seen) == len(list(Crawler(crawl_directory_path)))


def test_apply_passes_path_instance(crawl_directory_path: Union[str, Path]):
    types_seen: list = []
    Crawler(crawl_directory_path).apply(lambda p: types_seen.append(isinstance(p, Path)))
    assert types_seen
    assert all(types_seen)


def test_apply_set_matches_iteration_set(crawl_directory_path: Union[str, Path]):
    seen: list = []
    Crawler(crawl_directory_path).apply(seen.append)
    assert set(seen) == set(Crawler(crawl_directory_path))


def test_apply_order_matches_iteration(crawl_directory_path: Union[str, Path]):
    seen: list = []
    Crawler(crawl_directory_path).apply(seen.append)
    assert seen == list(Crawler(crawl_directory_path))


def test_apply_returns_none(crawl_directory_path: Union[str, Path]):
    assert Crawler(crawl_directory_path).apply(lambda x: x) is None  # type: ignore[func-returns-value]


def test_apply_multiple_invocations_independent(crawl_directory_path: Union[str, Path]):
    crawler = Crawler(crawl_directory_path)
    first: list = []
    second: list = []
    crawler.apply(first.append)
    crawler.apply(second.append)
    assert first == second
    assert first == list(crawler)


def test_apply_on_empty_directory(tmp_path: Path):
    seen: list = []
    Crawler(tmp_path).apply(seen.append)
    assert seen == []


def test_apply_respects_extensions(crawl_directory_path: Union[str, Path]):
    seen: list = []
    Crawler(crawl_directory_path, extensions=['.py']).apply(seen.append)
    assert seen
    assert all(p.suffix == '.py' for p in seen)


def test_apply_respects_exclude(crawl_directory_path: Union[str, Path]):
    seen: list = []
    Crawler(crawl_directory_path, exclude=['__init__.py']).apply(seen.append)
    assert seen
    assert all(p.name != '__init__.py' for p in seen)


def test_apply_respects_custom_filter(crawl_directory_path: Union[str, Path]):
    seen: list = []
    Crawler(crawl_directory_path, filter=lambda x: x.suffix == '.py').apply(seen.append)
    assert seen
    assert all(p.suffix == '.py' for p in seen)


def test_apply_respects_all_filters_combined(crawl_directory_path: Union[str, Path]):
    seen: list = []
    kwargs = dict(
        extensions=['.py'],
        exclude=['__init__.py'],
        filter=lambda x: 'simple' in x.name,
    )
    Crawler(crawl_directory_path, **kwargs).apply(seen.append)  # type: ignore[arg-type]
    assert seen == list(Crawler(crawl_directory_path, **kwargs))  # type: ignore[arg-type]


def test_apply_with_cancelled_call_time_token_skips_callback(crawl_directory_path: Union[str, Path]):
    seen: list = []
    Crawler(crawl_directory_path).apply(seen.append, token=SimpleToken(cancelled=True))
    assert seen == []


def test_apply_with_cancelled_instance_token_skips_callback(crawl_directory_path: Union[str, Path]):
    seen: list = []
    Crawler(crawl_directory_path, token=SimpleToken(cancelled=True)).apply(seen.append)
    assert seen == []


@pytest.mark.parametrize('n', [0, 1, 2, 3])
def test_apply_with_condition_token_cancels_after_n(crawl_directory_path: Union[str, Path], n: int):
    seen: list = []
    index = 0

    def condition() -> bool:
        return index == n

    def callback(path: Path) -> None:
        nonlocal index
        seen.append(path)
        index += 1

    Crawler(crawl_directory_path).apply(callback, token=ConditionToken(condition))
    assert seen == list(Crawler(crawl_directory_path))[:n]


@pytest.mark.parametrize(
    ('instance_cancelled', 'apply_cancelled'),
    [
        (True, False),
        (False, True),
        (True, True),
    ],
)
def test_apply_combines_instance_and_call_tokens(
    crawl_directory_path: Union[str, Path],
    instance_cancelled: bool,
    apply_cancelled: bool,
):
    seen: list = []
    crawler = Crawler(crawl_directory_path, token=SimpleToken(cancelled=instance_cancelled))
    crawler.apply(seen.append, token=SimpleToken(cancelled=apply_cancelled))
    assert seen == []


def test_apply_default_token_walks_everything(crawl_directory_path: Union[str, Path]):
    seen: list = []
    Crawler(crawl_directory_path).apply(seen.append)
    assert seen == list(Crawler(crawl_directory_path))


def test_apply_token_check_granularity_is_between_yields(crawl_directory_path: Union[str, Path]):
    seen: list = []
    cancelled_flag = False

    def condition() -> bool:
        return cancelled_flag

    def callback(path: Path) -> None:
        nonlocal cancelled_flag
        seen.append(path)
        cancelled_flag = True

    Crawler(crawl_directory_path).apply(callback, token=ConditionToken(condition))
    assert len(seen) == 1


def test_apply_with_zero_arg_callable_raises(crawl_directory_path: Union[str, Path]):
    with pytest.raises(SignatureMismatchError, match=match('The signature of the callable object does not match the expected one.')):
        Crawler(crawl_directory_path).apply(lambda: None)  # type: ignore[misc, arg-type]


def test_apply_with_two_arg_callable_raises(crawl_directory_path: Union[str, Path]):
    with pytest.raises(SignatureMismatchError):
        Crawler(crawl_directory_path).apply(lambda x, y: None)  # type: ignore[misc, arg-type]  # noqa: ARG005


def test_apply_with_def_function_works(crawl_directory_path: Union[str, Path]):
    seen: list = []

    def callback(path: Path) -> None:
        seen.append(path)

    Crawler(crawl_directory_path).apply(callback)
    assert seen == list(Crawler(crawl_directory_path))


def test_apply_validation_raises_before_iteration(crawl_directory_path: Union[str, Path]):
    with pytest.raises(SignatureMismatchError):
        Crawler(crawl_directory_path).apply(lambda: None)  # type: ignore[misc, arg-type]


def test_apply_validation_runs_at_apply_not_construction(crawl_directory_path: Union[str, Path]):
    crawler = Crawler(crawl_directory_path)
    with pytest.raises(SignatureMismatchError):
        crawler.apply(lambda: None)  # type: ignore[misc, arg-type]


def test_apply_with_callable_class_instance(crawl_directory_path: Union[str, Path]):
    class Recorder:
        def __init__(self) -> None:
            self.seen: list = []

        def __call__(self, path: Path) -> None:
            self.seen.append(path)

    recorder = Recorder()
    Crawler(crawl_directory_path).apply(recorder)
    assert recorder.seen == list(Crawler(crawl_directory_path))


def test_apply_with_functools_partial(crawl_directory_path: Union[str, Path]):
    seen: list = []

    def cb(prefix: str, path: Path) -> None:
        seen.append((prefix, path))

    Crawler(crawl_directory_path).apply(partial(cb, 'hit'))
    expected = list(Crawler(crawl_directory_path))
    assert seen == [('hit', p) for p in expected]


def test_apply_with_bound_method(crawl_directory_path: Union[str, Path]):
    class Collector:
        def __init__(self) -> None:
            self.seen: list = []

        def cb(self, path: Path) -> None:
            self.seen.append(path)

    c = Collector()
    Crawler(crawl_directory_path).apply(c.cb)
    assert c.seen == list(Crawler(crawl_directory_path))


def test_apply_with_generator_function_is_silent_noop(crawl_directory_path: Union[str, Path]):
    # A def with `yield` returns a generator object without executing the body.
    # apply() does not consume that generator, so side effects never fire.
    counter: list = []

    def gen(path: Path):
        counter.append(path)
        yield path

    Crawler(crawl_directory_path).apply(gen)
    assert counter == []


def test_apply_propagates_exception(crawl_directory_path: Union[str, Path]):
    def boom(path: Path) -> None:  # noqa: ARG001
        raise ValueError('boom')

    with pytest.raises(ValueError, match=match('boom')):
        Crawler(crawl_directory_path).apply(boom)


def test_apply_stops_iteration_on_first_exception(crawl_directory_path: Union[str, Path]):
    counter = 0

    def callback(path: Path) -> None:  # noqa: ARG001
        nonlocal counter
        counter += 1
        if counter == 3:
            raise ValueError('stop here')

    with pytest.raises(ValueError, match=match('stop here')):
        Crawler(crawl_directory_path).apply(callback)
    assert counter == 3


def test_apply_preserves_custom_exception_type(crawl_directory_path: Union[str, Path]):
    class MyError(Exception):
        pass

    def callback(path: Path) -> None:  # noqa: ARG001
        raise MyError('custom')

    with pytest.raises(MyError):
        Crawler(crawl_directory_path).apply(callback)


def test_apply_on_group_visits_paths_from_both(
    crawl_directory_path: Union[str, Path],
    second_crawl_directory_path: Union[str, Path],
):
    seen: list = []
    group = Crawler(crawl_directory_path) + Crawler(second_crawl_directory_path)
    group.apply(seen.append)
    assert set(seen) == set(Crawler(crawl_directory_path)) | set(Crawler(second_crawl_directory_path))


def test_apply_on_group_deduplicates(crawl_directory_path: Union[str, Path]):
    seen: list = []
    group = Crawler(crawl_directory_path) + Crawler(crawl_directory_path)
    group.apply(seen.append)
    assert seen == list(Crawler(crawl_directory_path))


def test_apply_on_nested_group_deduplicates(crawl_directory_path: Union[str, Path]):
    seen: list = []
    group = Crawler(crawl_directory_path) + (Crawler(crawl_directory_path) + Crawler(crawl_directory_path))
    group.apply(seen.append)
    assert seen == list(Crawler(crawl_directory_path))


def test_apply_on_group_with_cancelled_token(
    crawl_directory_path: Union[str, Path],
    second_crawl_directory_path: Union[str, Path],
):
    seen: list = []
    group = Crawler(crawl_directory_path) + Crawler(second_crawl_directory_path)
    group.apply(seen.append, token=SimpleToken(cancelled=True))
    assert seen == []


def test_apply_on_group_respects_child_tokens(
    crawl_directory_path: Union[str, Path],
    second_crawl_directory_path: Union[str, Path],
):
    seen: list = []
    live = Crawler(crawl_directory_path)
    dead = Crawler(second_crawl_directory_path, token=SimpleToken(cancelled=True))
    (live + dead).apply(seen.append)
    assert set(seen) == set(live)


def test_apply_with_multipath_crawler_no_dedup(crawl_directory_path: Union[str, Path]):
    seen: list = []
    Crawler(crawl_directory_path, crawl_directory_path).apply(seen.append)
    expected = list(Crawler(crawl_directory_path)) * 2
    seen_sorted = sorted(str(p) for p in seen)
    expected_sorted = sorted(str(p) for p in expected)
    assert seen_sorted == expected_sorted


def test_apply_with_none_raises_valueerror(crawl_directory_path: Union[str, Path]):
    with pytest.raises(ValueError, match=match('It is impossible to determine the signature of an object that is not being callable.')):
        Crawler(crawl_directory_path).apply(None)  # type: ignore[arg-type]


def test_apply_on_zero_path_crawler_never_calls_callback():
    seen: list = []
    Crawler().apply(seen.append)
    assert seen == []


def test_apply_on_nonexistent_base_path_matches_iteration_behavior(tmp_path: Path):
    nonexistent = tmp_path / 'does_not_exist'

    iter_error: type = type(None)
    try:
        list(Crawler(nonexistent))
    except Exception as e:  # noqa: BLE001
        iter_error = type(e)

    seen: list = []
    apply_error: type = type(None)
    try:
        Crawler(nonexistent).apply(seen.append)
    except Exception as e:  # noqa: BLE001
        apply_error = type(e)

    assert apply_error == iter_error
    if iter_error is type(None):
        assert seen == []


def test_apply_on_file_base_path_matches_iteration_behavior(tmp_path: Path):
    file_path = tmp_path / 'a_file.txt'
    file_path.write_text('hi')

    iter_paths = list(Crawler(file_path))
    seen: list = []
    Crawler(file_path).apply(seen.append)
    assert seen == iter_paths
