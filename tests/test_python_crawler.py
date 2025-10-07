import os
from typing import Union
from pathlib import Path
from inspect import signature

import pytest
from cantok import ConditionToken

from dirstree import Crawler, PythonCrawler


def custom_filter(path: Path) -> bool:
    return True


def test_signature_of_python_crawler_is_signature_of_crawler_without_extensions():
    crawler_parameters = list(signature(Crawler).parameters.keys())
    crawler_parameters.remove('extensions')

    assert crawler_parameters == list(signature(PythonCrawler).parameters.keys())


def test_crawl_python_files_in_test_directory(
    crawl_directory_path: Union[str, Path],
):
    crawler = PythonCrawler(crawl_directory_path)

    expected_paths = [
        os.path.join('tests', 'test_files', 'walk_it', '__init__.py'),
        os.path.join('tests', 'test_files', 'walk_it', 'simple_code.py'),
        os.path.join(
            'tests', 'test_files', 'walk_it', 'nested_folder', 'python_file.py'
        ),
        os.path.join('tests', 'test_files', 'walk_it', 'nested_folder', '__init__.py'),
    ]
    real_paths = [str(x) for x in crawler.go()]

    expected_paths.sort()
    real_paths.sort()

    assert real_paths == expected_paths


def test_python_crawler_is_same_as_crawler_with_python_extension(crawl_directory_path):
    assert list(PythonCrawler(crawl_directory_path)) == list(
        Crawler(crawl_directory_path, extensions=['.py'])
    )


def test_cant_pass_extensions():
    with pytest.raises(TypeError):
        PythonCrawler('.', extensions=['.txt'])


def test_crawl_test_directory_with_exclude_inits(
    crawl_directory_path: Union[str, Path],
):
    crawler = PythonCrawler(crawl_directory_path, exclude=['__init__.py'])

    assert [str(x) for x in crawler] == [
        os.path.join('tests', 'test_files', 'walk_it', 'simple_code.py'),
        os.path.join(
            'tests', 'test_files', 'walk_it', 'nested_folder', 'python_file.py'
        ),
    ]


@pytest.mark.parametrize(
    ['crawler', 'expected_repr'],
    [
        (PythonCrawler('.'), "PythonCrawler('.')"),
        (PythonCrawler('usr/bin'), "PythonCrawler('usr/bin')"),
        (PythonCrawler('.', exclude=['*.py']), "PythonCrawler('.', exclude=['*.py'])"),
        (PythonCrawler('.', filter=custom_filter), "PythonCrawler('.', filter=custom_filter)"),
        (PythonCrawler('.', filter=lambda x: True), "PythonCrawler('.', filter=λ)"),
        (PythonCrawler('.', token=ConditionToken(lambda: True)), "PythonCrawler('.', token=ConditionToken(λ))"),
    ],
)
def test_python_crawler_repr(crawler, expected_repr):
    assert repr(crawler) == expected_repr


@pytest.mark.parametrize(
    ['n'],
    [
        (0,),
        (1,),
        (2,),
        (3,),
    ],
)
def test_cancel_after_n_iteranions(crawl_directory_path: Union[str, Path], n: int):
    index = 0

    def filter(path: str) -> bool:
        nonlocal index
        index += 1
        return True

    def condition() -> bool:
        if index == n:
            result = True
        else:
            result = False

        return result

    token = ConditionToken(condition)

    crawler = PythonCrawler(crawl_directory_path, token=token, filter=filter)

    assert list(PythonCrawler(crawl_directory_path))[:n] == list(crawler)


def test_sum_of_same_python_crawlers(crawl_directory_path: Union[str, Path]):
    assert list(PythonCrawler(crawl_directory_path) + PythonCrawler(crawl_directory_path)) == list(PythonCrawler(crawl_directory_path))


def test_sum_of_same_python_crawlers_for_current_directory():
    assert list(PythonCrawler('.') + PythonCrawler('.')) == list(PythonCrawler('.'))
