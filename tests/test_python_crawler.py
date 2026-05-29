import os
import sys
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
    """
    `PythonCrawler` should yield Python files from the fixture tree.

    The test compares sorted string paths from `go()` with the expected `.py`
    files, including nested package files.
    """
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
    """
    `PythonCrawler` should be equivalent to `Crawler(..., extensions=['.py'])`.

    The test compares both traversals over the same fixture path.
    """
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
    if sys.version_info < (3, 10):
        expected_message = "__init__() got an unexpected keyword argument 'extensions'"
    else:
        expected_message = "PythonCrawler.__init__() got an unexpected keyword argument 'extensions'"

    with pytest.raises(TypeError, match=match(expected_message)):
        PythonCrawler('.', extensions=['.txt'])


def test_python_crawler_rejects_only_files(crawl_directory_path: Union[str, Path]):
    """
    `PythonCrawler` should reject the all-entity crawler mode.

    The test passes `only_files=False` and verifies that the constructor raises
    `TypeError` because the subclass does not expose that parameter.
    """
    if sys.version_info < (3, 10):
        expected_message = "__init__() got an unexpected keyword argument 'only_files'"
    else:
        expected_message = "PythonCrawler.__init__() got an unexpected keyword argument 'only_files'"

    with pytest.raises(TypeError, match=match(expected_message)):
        PythonCrawler(crawl_directory_path, only_files=False)  # type: ignore[call-arg]


def test_crawl_test_directory_with_exclude_inits(
    crawl_directory_path: Union[str, Path],
):
    """
    `PythonCrawler` should respect exclude patterns.

    The test excludes `__init__.py` and verifies that only the non-init Python
    files from the fixture remain.
    """
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
    """
    `PythonCrawler` and its groups should have stable representations.

    The parametrized cases check exact `repr()` output for paths, excludes,
    filters, tokens, and PythonCrawler groups.
    """
    assert repr(crawler) == expected_repr


def test_sum_of_same_python_crawlers(crawl_directory_path: Union[str, Path]):
    """
    A group made from duplicate Python crawlers should deduplicate paths.

    The test adds two equivalent `PythonCrawler` instances and compares the
    group result with one crawler traversal.
    """
    assert list(PythonCrawler(crawl_directory_path) + PythonCrawler(crawl_directory_path)) == list(PythonCrawler(crawl_directory_path))


def test_sum_of_same_python_crawlers_for_current_directory():
    """
    Duplicate Python crawlers should deduplicate for the current directory too.

    The test adds two `PythonCrawler('.')` instances and expects the same result
    as a single current-directory Python crawl.
    """
    assert list(PythonCrawler('.') + PythonCrawler('.')) == list(PythonCrawler('.'))


def test_crawl_two_folders(crawl_directory_path: Union[str, Path], second_crawl_directory_path: Union[str, Path]):
    """
    A multipath Python crawler should traverse base paths in argument order.

    The test compares one two-path `PythonCrawler` with the concatenation of two
    single-path Python crawler results.
    """
    assert list(PythonCrawler(crawl_directory_path, second_crawl_directory_path)) == list(PythonCrawler(crawl_directory_path)) + list(PythonCrawler(second_crawl_directory_path))


def test_crawl_without_path():
    """
    `PythonCrawler` without base paths should yield nothing.

    The test constructs `PythonCrawler()` and expects an empty list.
    """
    assert list(PythonCrawler()) == []


def test_check_filter_signature():
    """
    `PythonCrawler` should validate filter callable signatures.

    The test passes a zero-argument filter and verifies the signature validation
    error.
    """
    with pytest.raises(SignatureMismatchError, match=match('The signature of the callable object does not match the expected one.')):
        PythonCrawler(filter=lambda: None)


def test_python_apply_only_visits_py_files(crawl_directory_path: Union[str, Path]):
    """
    `PythonCrawler.apply()` should visit only Python files.

    The test records callback inputs, verifies every suffix is `.py`, and
    compares the callback order with normal PythonCrawler iteration.
    """
    seen: list = []
    PythonCrawler(crawl_directory_path).apply(seen.append)
    assert seen
    assert all(p.suffix == '.py' for p in seen)
    assert seen == list(PythonCrawler(crawl_directory_path))


def test_python_apply_respects_exclude(crawl_directory_path: Union[str, Path]):
    """
    `PythonCrawler.apply()` should respect exclude patterns.

    The test excludes `__init__.py`, records callback inputs, and compares them
    with normal iteration using the same exclude option.
    """
    seen: list = []
    PythonCrawler(crawl_directory_path, exclude=['__init__.py']).apply(seen.append)
    assert seen == list(PythonCrawler(crawl_directory_path, exclude=['__init__.py']))


def test_python_apply_respects_custom_filter(crawl_directory_path: Union[str, Path]):
    """
    `PythonCrawler.apply()` should respect custom filters.

    The test keeps only paths whose names contain `simple` and verifies that
    every callback input matches that predicate.
    """
    seen: list = []
    PythonCrawler(crawl_directory_path, filter=lambda x: 'simple' in x.name).apply(seen.append)
    assert seen
    assert all('simple' in p.name for p in seen)


def test_python_apply_with_cancelled_token(crawl_directory_path: Union[str, Path]):
    """
    `PythonCrawler.apply()` should respect an already-cancelled instance token.

    The test creates a cancelled PythonCrawler and verifies that `apply()` does
    not invoke the callback.
    """
    seen: list = []
    PythonCrawler(crawl_directory_path, token=SimpleToken(cancelled=True)).apply(seen.append)
    assert seen == []
