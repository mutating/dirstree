import os
from inspect import signature
from pathlib import Path
from typing import Union

import pytest
from cantok import ConditionToken, SimpleToken
from full_match import match
from sigmatch.errors import SignatureMismatchError

from dirstree import Crawler, PythonCrawler


def custom_filter(path: Path) -> bool:  # noqa: ARG001
    return True


def test_signature_of_python_crawler_is_signature_of_crawler_without_extensions():
    """
    `PythonCrawler` should not expose generic file-selection options.

    The test compares constructor parameters with `Crawler` after removing
    `extensions` and `only_files`, because PythonCrawler is always file-only and
    fixed to Python files.
    """
    crawler_parameters = list(signature(Crawler).parameters.keys())
    crawler_parameters.remove('extensions')
    crawler_parameters.remove('only_files')

    assert crawler_parameters == list(signature(PythonCrawler).parameters.keys())


def test_crawl_python_files_in_test_directory(
    crawl_directory_path: Union[str, Path],
):
    crawler = PythonCrawler(crawl_directory_path)

    expected_paths = [
        os.path.join('tests', 'test_files', 'walk_it', '__init__.py'),
        os.path.join('tests', 'test_files', 'walk_it', 'simple_code.py'),
        os.path.join(
            'tests', 'test_files', 'walk_it', 'nested_folder', 'python_file.py',
        ),
        os.path.join('tests', 'test_files', 'walk_it', 'nested_folder', '__init__.py'),
    ]
    real_paths = [str(x) for x in crawler.go()]

    expected_paths.sort()
    real_paths.sort()

    assert real_paths == expected_paths


def test_python_crawler_is_same_as_crawler_with_python_extension(crawl_directory_path):
    assert list(PythonCrawler(crawl_directory_path)) == list(
        Crawler(crawl_directory_path, extensions=['.py']),
    )


def test_cant_pass_extensions():
    """
    `PythonCrawler` should reject explicit extension configuration.

    The test passes `extensions` as a keyword argument and verifies Python's
    constructor-level `TypeError`, because the subclass does not expose that
    parameter.
    """
    with pytest.raises(TypeError, match=match("PythonCrawler.__init__() got an unexpected keyword argument 'extensions'")):
        PythonCrawler('.', extensions=['.txt'])


def test_python_crawler_rejects_only_files(crawl_directory_path: Union[str, Path]):
    """
    `PythonCrawler` should reject the all-entity crawler mode.

    The test passes `only_files=False` and verifies that the constructor raises
    `TypeError` because the subclass does not expose that parameter.
    """
    with pytest.raises(TypeError, match=match("PythonCrawler.__init__() got an unexpected keyword argument 'only_files'")):
        PythonCrawler(crawl_directory_path, only_files=False)  # type: ignore[call-arg]


def test_crawl_test_directory_with_exclude_inits(
    crawl_directory_path: Union[str, Path],
):
    crawler = PythonCrawler(crawl_directory_path, exclude=['__init__.py'])

    assert [str(x) for x in crawler] == [
        os.path.join('tests', 'test_files', 'walk_it', 'simple_code.py'),
        os.path.join(
            'tests', 'test_files', 'walk_it', 'nested_folder', 'python_file.py',
        ),
    ]


@pytest.mark.parametrize(
    ('crawler', 'expected_repr'),
    [
        (PythonCrawler('.'), "PythonCrawler('.')"),
        (PythonCrawler('usr/bin'), "PythonCrawler('usr/bin')"),
        (PythonCrawler('.', exclude=['*.py']), "PythonCrawler('.', exclude=['*.py'])"),
        (PythonCrawler('.', filter=custom_filter), "PythonCrawler('.', filter=custom_filter)"),
        (PythonCrawler('.', filter=lambda x: True), "PythonCrawler('.', filter=lambda x: True)"),  # noqa: ARG005
        (PythonCrawler('.', token=ConditionToken(lambda: True)), "PythonCrawler('.', token=ConditionToken(λ))"),
        (PythonCrawler('../dirstree') + PythonCrawler('../cantok'), "CrawlersGroup([PythonCrawler('../dirstree'), PythonCrawler('../cantok')])"),
    ],
)
def test_python_crawler_repr(crawler, expected_repr):
    assert repr(crawler) == expected_repr


def test_sum_of_same_python_crawlers(crawl_directory_path: Union[str, Path]):
    assert list(PythonCrawler(crawl_directory_path) + PythonCrawler(crawl_directory_path)) == list(PythonCrawler(crawl_directory_path))


def test_sum_of_same_python_crawlers_for_current_directory():
    assert list(PythonCrawler('.') + PythonCrawler('.')) == list(PythonCrawler('.'))


def test_crawl_two_folders(crawl_directory_path: Union[str, Path], second_crawl_directory_path: Union[str, Path]):
    assert list(PythonCrawler(crawl_directory_path, second_crawl_directory_path)) == list(PythonCrawler(crawl_directory_path)) + list(PythonCrawler(second_crawl_directory_path))


def test_crawl_without_path():
    assert list(PythonCrawler()) == []


def test_check_filter_signature():
    with pytest.raises(SignatureMismatchError, match=match('The signature of the callable object does not match the expected one.')):
        PythonCrawler(filter=lambda: None)


def test_python_apply_only_visits_py_files(crawl_directory_path: Union[str, Path]):
    seen: list = []
    PythonCrawler(crawl_directory_path).apply(seen.append)
    assert seen
    assert all(p.suffix == '.py' for p in seen)
    assert seen == list(PythonCrawler(crawl_directory_path))


def test_python_apply_respects_exclude(crawl_directory_path: Union[str, Path]):
    seen: list = []
    PythonCrawler(crawl_directory_path, exclude=['__init__.py']).apply(seen.append)
    assert seen == list(PythonCrawler(crawl_directory_path, exclude=['__init__.py']))


def test_python_apply_respects_custom_filter(crawl_directory_path: Union[str, Path]):
    seen: list = []
    PythonCrawler(crawl_directory_path, filter=lambda x: 'simple' in x.name).apply(seen.append)
    assert seen
    assert all('simple' in p.name for p in seen)


def test_python_apply_with_cancelled_token(crawl_directory_path: Union[str, Path]):
    seen: list = []
    PythonCrawler(crawl_directory_path, token=SimpleToken(cancelled=True)).apply(seen.append)
    assert seen == []
