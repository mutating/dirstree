import os
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
        (Crawler('.', filter=lambda x: True), "Crawler('.', filter=λ)"),  # noqa: ARG005
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
