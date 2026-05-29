import errno
import os
import stat
from functools import partial
from inspect import Parameter, signature
from pathlib import Path
from typing import List, Type, Union

import pytest
from cantok import ConditionToken, DefaultToken, SimpleToken
from full_match import match
from sigmatch.errors import SignatureMismatchError

from dirstree import (
    Crawler,
    IncompatibleCrawlerOptionsError,
    PythonCrawler,
)

INCOMPATIBLE_OPTIONS_MESSAGE = (
    'The "extensions" and "only_files=False" options are incompatible: '
    'extensions can be applied only when the crawler yields files, '
    'because non-file filesystem entities do not have meaningful file extensions.'
)


def custom_filter(path: Path) -> bool:  # noqa: ARG001
    return True


def test_only_files_false_yields_files_and_directories(all_entities_directory_path: Union[str, Path]):
    """
    Crawling all entities should include both files and directories.

    The test compares the full result set against a fixture tree containing
    regular files, a nested directory, and files inside that directory.
    """
    base_path = Path(all_entities_directory_path)

    assert set(Crawler(all_entities_directory_path, only_files=False)) == {
        base_path / '__init__.py',
        base_path / 'simple_code.py',
        base_path / '.hidden_file',
        base_path / '.hidden_folder',
        base_path / '.hidden_folder' / 'inside.txt',
        base_path / 'nested_folder',
        base_path / 'nested_folder' / '__init__.py',
        base_path / 'nested_folder' / 'non_python_file.txt',
        base_path / 'nested_folder' / 'python_file.py',
    }


def test_go_with_only_files_false_matches_iteration(all_entities_directory_path: Union[str, Path]):
    """
    Explicit `go()` calls should use the same all-entity traversal as iteration.

    The test compares a crawler's `go()` output to `list(crawler)` for the same
    `only_files=False` instance.
    """
    crawler = Crawler(all_entities_directory_path, only_files=False)

    assert list(crawler.go()) == list(crawler)


def test_only_files_is_keyword_only():
    """
    The new `only_files` option should not consume positional arguments.

    The test inspects the public constructor signature and checks that the
    parameter is keyword-only.
    """
    assert signature(Crawler).parameters['only_files'].kind is Parameter.KEYWORD_ONLY


def test_only_files_false_yields_empty_directories(tmp_path: Path):
    """
    Empty directories should be yielded when all filesystem entities are crawled.

    The test creates an otherwise empty directory and checks that it appears in
    the result of crawling its parent with `only_files=False`.
    """
    empty_folder = tmp_path / 'empty_folder'
    empty_folder.mkdir()

    assert empty_folder in set(Crawler(tmp_path, only_files=False))


def test_only_files_false_yields_hidden_paths(all_entities_directory_path: Union[str, Path]):
    """
    Hidden paths should not be filtered out by the all-entity mode.

    The test uses fixture entries whose names start with a dot and verifies that
    both the hidden file and hidden directory are yielded.
    """
    paths = set(Crawler(all_entities_directory_path, only_files=False))

    assert Path(all_entities_directory_path) / '.hidden_file' in paths
    assert Path(all_entities_directory_path) / '.hidden_folder' in paths


def test_only_files_false_does_not_yield_base_path(all_entities_directory_path: Union[str, Path]):
    """
    Crawling all entities should not yield the base path itself.

    The test verifies that the root passed to the crawler is absent from the
    yielded paths.
    """
    assert Path(all_entities_directory_path) not in set(Crawler(all_entities_directory_path, only_files=False))


def test_default_mode_stays_file_only(all_entities_directory_path: Union[str, Path]):
    """
    The default crawler mode should remain file-only.

    The test uses a fixture that also contains directories, then asserts both
    the exact expected file set and that every yielded path is a file.
    """
    base_path = Path(all_entities_directory_path)
    expected_paths = {
        base_path / '__init__.py',
        base_path / 'simple_code.py',
        base_path / '.hidden_file',
        base_path / '.hidden_folder' / 'inside.txt',
        base_path / 'nested_folder' / '__init__.py',
        base_path / 'nested_folder' / 'non_python_file.txt',
        base_path / 'nested_folder' / 'python_file.py',
    }
    real_paths = set(Crawler(all_entities_directory_path))

    assert real_paths == expected_paths
    assert all(path.is_file() for path in real_paths)


def test_zero_paths_with_only_files_false_returns_empty_list():
    """
    A crawler without base paths should be empty in all-entity mode.

    The test constructs a zero-path crawler with `only_files=False` and verifies
    that iteration returns an empty list.
    """
    assert list(Crawler(only_files=False)) == []


def test_empty_base_directory_with_only_files_false_returns_empty_list(tmp_path: Path):
    """
    An empty base directory should not yield itself.

    The test crawls an empty temporary directory with `only_files=False` and
    expects no child paths.
    """
    assert list(Crawler(tmp_path, only_files=False)) == []


def test_nonexistent_base_path_with_only_files_false_returns_empty_list(tmp_path: Path):
    """
    A nonexistent base path should yield no results in all-entity mode.

    The test points the crawler at a missing path and verifies that no entries
    are yielded in all-entity mode.
    """
    assert list(Crawler(tmp_path / 'missing', only_files=False)) == []


def test_file_base_path_with_only_files_false_returns_empty_list(tmp_path: Path):
    """
    A file used as the base path should not be yielded as its own child.

    The test creates a file, crawls it as the base path with `only_files=False`,
    and expects no results.
    """
    file_path = tmp_path / 'file.py'
    file_path.write_text('content')

    assert list(Crawler(file_path, only_files=False)) == []


@pytest.mark.parametrize(
    'exclude',
    [
        ['nested_folder/'],
        ['nested_folder'],
    ],
)
def test_exclude_directory_pattern_excludes_directory_and_children(
    all_entities_directory_path: Union[str, Path],
    exclude: list,
):
    """
    Directory exclude patterns should remove both the directory and its children.

    The test checks gitwildmatch-style patterns with and without a trailing slash
    against a nested fixture directory, and also verifies that unrelated paths
    remain visible.
    """
    base_path = Path(all_entities_directory_path)
    paths = set(Crawler(all_entities_directory_path, only_files=False, exclude=exclude))

    assert base_path / 'nested_folder' not in paths
    assert base_path / 'nested_folder' / '__init__.py' not in paths
    assert base_path / 'nested_folder' / 'non_python_file.txt' not in paths
    assert base_path / 'nested_folder' / 'python_file.py' not in paths
    assert base_path / '__init__.py' in paths
    assert base_path / 'simple_code.py' in paths
    assert base_path / '.hidden_file' in paths
    assert base_path / '.hidden_folder' in paths
    assert base_path / '.hidden_folder' / 'inside.txt' in paths


def test_filter_is_called_for_files_and_directories_with_only_files_false(all_entities_directory_path: Union[str, Path]):
    """
    Custom filters should see both files and directories in all-entity mode.

    The test records every path passed to the filter and verifies that the
    callback saw both files and directories as `Path` objects.
    """
    seen = []

    def collect(path: Path) -> bool:
        seen.append(path)
        return True

    list(Crawler(all_entities_directory_path, only_files=False, filter=collect))

    assert any(path.is_file() for path in seen)
    assert any(path.is_dir() for path in seen)
    assert all(isinstance(path, Path) for path in seen)


def test_filter_false_for_directory_does_not_prune_children(all_entities_directory_path: Union[str, Path]):
    """
    A false filter result should hide only the current path, not its descendants.

    The test rejects the nested directory itself and verifies that a file inside
    that directory can still be yielded.
    """
    base_path = Path(all_entities_directory_path)
    paths = set(
        Crawler(
            all_entities_directory_path,
            only_files=False,
            filter=lambda path: path.name != 'nested_folder',
        ),
    )

    assert base_path / 'nested_folder' not in paths
    assert base_path / 'nested_folder' / 'python_file.py' in paths


@pytest.mark.parametrize('n', [0, 1, 2, 3])
def test_cancel_after_n_iterations_with_only_files_false(all_entities_directory_path: Union[str, Path], n: int):
    """
    Cancellation should stop all-entity traversal between yielded paths.

    The test increments a counter from the filter and uses a condition token to
    cancel after the filter has seen `n` candidates, then compares with the uncancelled
    prefix.
    """
    index = 0

    def count(path: Path) -> bool:  # noqa: ARG001
        nonlocal index
        index += 1
        return True

    def condition() -> bool:
        return index == n

    token = ConditionToken(condition)
    crawler = Crawler(all_entities_directory_path, only_files=False, token=token, filter=count)

    assert list(crawler) == list(Crawler(all_entities_directory_path, only_files=False))[:n]


def test_multiple_base_paths_with_only_files_false_are_not_deduplicated(all_entities_directory_path: Union[str, Path]):
    """
    Multiple base paths on one crawler should preserve the existing no-dedup rule.

    The test crawls the same base path twice in one crawler and compares sorted
    string paths with two copies of a single-base traversal.
    """
    real_paths = sorted(
        str(path) for path in Crawler(all_entities_directory_path, all_entities_directory_path, only_files=False)
    )
    expected_paths = sorted(str(path) for path in list(Crawler(all_entities_directory_path, only_files=False)) * 2)

    assert real_paths == expected_paths


def test_apply_with_only_files_false_matches_iteration_order(all_entities_directory_path: Union[str, Path]):
    """
    `apply()` should visit the same paths in the same order as iteration.

    The test records callback inputs and compares them to a normal
    `only_files=False` traversal.
    """
    seen: List[Path] = []

    Crawler(all_entities_directory_path, only_files=False).apply(seen.append)

    assert seen == list(Crawler(all_entities_directory_path, only_files=False))


def test_apply_with_only_files_false_passes_directories_to_callback(all_entities_directory_path: Union[str, Path]):
    """
    `apply()` should pass directories to the callback in all-entity mode.

    The test collects callback inputs and checks that at least one yielded path
    is a directory.
    """
    seen: List[Path] = []

    Crawler(all_entities_directory_path, only_files=False).apply(seen.append)

    assert any(path.is_dir() for path in seen)


def test_group_with_only_files_false_deduplicates_paths(all_entities_directory_path: Union[str, Path]):
    """
    Groups should deduplicate overlap between all-entity and file-only crawlers.

    The test combines an all-entity crawler with a default crawler over the same
    base path and expects the all-entity traversal once.
    """
    group = Crawler(all_entities_directory_path, only_files=False) + Crawler(all_entities_directory_path)

    assert list(group) == list(Crawler(all_entities_directory_path, only_files=False))


def test_group_deduplicates_by_path_without_resolving(tmp_path: Path):
    """
    Group deduplication should preserve distinct paths to the same target.

    The test crawls a real directory and a symlink to it. The same target file is
    yielded through two different path prefixes, and both should remain.
    """
    real_directory = tmp_path / 'real'
    link_directory = tmp_path / 'link'
    real_file = real_directory / 'file.txt'
    link_file = link_directory / 'file.txt'
    real_directory.mkdir()
    real_file.write_text('content')

    try:
        link_directory.symlink_to(real_directory, target_is_directory=True)
    except (NotImplementedError, OSError) as e:
        pytest.skip(f'Symlinks are not supported here: {e}')

    paths = list(Crawler(real_directory, only_files=False) + Crawler(link_directory, only_files=False))

    assert paths == [real_file, link_file]


def test_repr_default_only_files_is_unchanged(all_entities_directory_path: Union[str, Path]):
    """
    The default `repr` should not show the new option.

    The test checks the exact representation for a default crawler so that
    `only_files=True` does not add noise.
    """
    assert repr(Crawler(all_entities_directory_path)) == f"Crawler({all_entities_directory_path!r})"


def test_repr_includes_only_files_when_false(all_entities_directory_path: Union[str, Path]):
    """
    The non-default all-entity mode should be visible in `repr`.

    The test checks the exact representation for `only_files=False`.
    """
    assert repr(Crawler(all_entities_directory_path, only_files=False)) == f"Crawler({all_entities_directory_path!r}, only_files=False)"


def test_non_empty_extensions_with_only_files_false_raise(all_entities_directory_path: Union[str, Path]):
    """
    Extension filtering should be rejected in all-entity mode.

    The test passes a normal non-empty extension list with `only_files=False` and
    verifies the dedicated incompatible-options error and message.
    """
    with pytest.raises(IncompatibleCrawlerOptionsError, match=match(INCOMPATIBLE_OPTIONS_MESSAGE)):
        Crawler(all_entities_directory_path, extensions=['.py'], only_files=False)


@pytest.mark.parametrize('extensions', [[], (), set(), frozenset()])
def test_empty_extensions_with_only_files_false_raise(all_entities_directory_path: Union[str, Path], extensions):
    """
    Any explicit extensions collection should be rejected in all-entity mode.

    The test parametrizes empty collection types to ensure that `None` is the
    only accepted "no extension filter" value with `only_files=False`.
    """
    with pytest.raises(IncompatibleCrawlerOptionsError, match=match(INCOMPATIBLE_OPTIONS_MESSAGE)):
        Crawler(all_entities_directory_path, extensions=extensions, only_files=False)


def test_incompatible_options_checked_before_extension_format(all_entities_directory_path: Union[str, Path]):
    """
    Incompatible mode options should take precedence over malformed extensions.

    The test uses an extension without a leading dot together with
    `only_files=False` and expects the incompatible-options error, not the older
    extension-format `ValueError`.
    """
    with pytest.raises(IncompatibleCrawlerOptionsError, match=match(INCOMPATIBLE_OPTIONS_MESSAGE)):
        Crawler(all_entities_directory_path, extensions=['py'], only_files=False)


def test_incompatible_options_error_message_mentions_both_options(all_entities_directory_path: Union[str, Path]):
    """
    The incompatible-options error should be diagnostic.

    The test checks both the full expected message and the important terms that
    explain which options conflict and why.
    """
    with pytest.raises(IncompatibleCrawlerOptionsError, match=match(INCOMPATIBLE_OPTIONS_MESSAGE)) as error:
        Crawler(all_entities_directory_path, extensions=['.py'], only_files=False)

    assert 'extensions' in str(error.value)
    assert 'only_files' in str(error.value)
    assert 'non-file filesystem entities' in str(error.value)


def test_only_files_false_yields_symlink_nodes_when_supported(tmp_path: Path):
    """
    All-entity mode should yield symlink entries under the base path.

    The test creates symlinks to a file, to a directory, and to a missing target,
    then verifies that each link path appears when symlinks are supported.
    """
    target_file = tmp_path / 'target.txt'
    target_directory = tmp_path / 'target_directory'
    file_link = tmp_path / 'file_link'
    directory_link = tmp_path / 'directory_link'
    broken_link = tmp_path / 'broken_link'
    target_file.write_text('target')
    target_directory.mkdir()

    try:
        file_link.symlink_to(target_file)
        directory_link.symlink_to(target_directory, target_is_directory=True)
        broken_link.symlink_to(tmp_path / 'missing')
    except (NotImplementedError, OSError) as e:
        pytest.skip(f'Symlinks are not supported here: {e}')

    paths = set(Crawler(tmp_path, only_files=False))

    assert file_link in paths
    assert directory_link in paths
    assert broken_link in paths


def test_rglob_errors_propagate_with_only_files_false(tmp_path: Path):
    """
    Traversal errors from `Path.rglob` should not be swallowed.

    The test creates an unreadable directory and first checks whether this
    platform exposes that as a `PermissionError`. If it does, the crawler must
    propagate the same error; otherwise the test is skipped.
    """
    blocked = tmp_path / 'blocked'
    blocked.mkdir()
    (blocked / 'file.txt').write_text('content')
    blocked.chmod(0)

    try:
        try:
            list(tmp_path.rglob('*'))
        except PermissionError:
            pass
        else:
            pytest.skip('Path.rglob does not propagate permission errors on this platform.')

        with pytest.raises(
            PermissionError,
            match=match(str(PermissionError(errno.EACCES, os.strerror(errno.EACCES), str(blocked)))),
        ):
            list(Crawler(tmp_path, only_files=False))
    finally:
        blocked.chmod(stat.S_IRWXU)


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
def test_filter_skips_first_path(factory: Type[Crawler]):
    """
    A false filter result should hide exactly the matching path.

    The test runs against both crawler classes with a stateful filter that
    rejects only the first candidate, then compares the result with the original
    traversal after removing its first path.
    """
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
def test_cancel_after_n_iterations(crawl_directory_path: Union[str, Path], n: int, factory: Type[Crawler]):
    """
    Cancellation should keep the same prefix behavior for both crawler classes.

    The test increments a counter from the filter, cancels when the counter
    reaches `n`, and checks that traversal returns the first `n` uncancelled
    paths.
    """
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


def test_extension_without_leading_dot_raises_error(crawl_directory_path: Union[str, Path]):
    """
    Extension strings should still be validated before crawling.

    The test passes an extension without the required leading dot and checks the
    exact `ValueError` message.
    """
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


def test_addition_with_non_crawler_raises_type_error():
    """
    Adding unsupported operand types to a crawler should fail with clear errors.

    The test tries both integer and string operands and verifies the exact
    `TypeError` messages.
    """
    with pytest.raises(TypeError, match=match('Cannot add Crawler and int.')):
        Crawler('.') + 1

    with pytest.raises(TypeError, match=match('Cannot add Crawler and str.')):
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
    """
    An explicit call-level default token should not restrict traversal.

    The test passes `DefaultToken()` to `apply()` and verifies that the callback
    receives the same paths as normal iteration.
    """
    seen: list = []
    Crawler(crawl_directory_path).apply(seen.append, token=DefaultToken())
    assert seen == list(Crawler(crawl_directory_path))


def test_apply_token_check_granularity_is_between_yields(crawl_directory_path: Union[str, Path]):
    """
    `apply()` should check cancellation between yielded paths.

    The callback flips a flag after the first path; the condition token observes
    that flag before the next callback, so exactly one path is visited.
    """
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
    with pytest.raises(
        SignatureMismatchError,
        match=match(
            'This is a difficult situation, there is no guarantee that a call with a variable number of positional arguments will fill all the slots of positional arguments.',
        ),
    ):
        Crawler(crawl_directory_path).apply(lambda x, y: None)  # type: ignore[misc, arg-type]  # noqa: ARG005


def test_apply_with_def_function_works(crawl_directory_path: Union[str, Path]):
    seen: list = []

    def callback(path: Path) -> None:
        seen.append(path)

    Crawler(crawl_directory_path).apply(callback)
    assert seen == list(Crawler(crawl_directory_path))


def test_apply_validation_raises_before_iteration(crawl_directory_path: Union[str, Path]):
    with pytest.raises(SignatureMismatchError, match=match('The signature of the callable object does not match the expected one.')):
        Crawler(crawl_directory_path).apply(lambda: None)  # type: ignore[misc, arg-type]


def test_apply_validation_runs_at_apply_not_construction(crawl_directory_path: Union[str, Path]):
    crawler = Crawler(crawl_directory_path)
    with pytest.raises(SignatureMismatchError, match=match('The signature of the callable object does not match the expected one.')):
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
    """
    Generator-function callbacks should keep Python's lazy execution behavior.

    A function containing `yield` returns a generator object without executing
    its body. Since `apply()` does not consume that generator, the side effect
    before `yield` never runs.
    """
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
    """
    `apply()` should stop immediately when the callback raises.

    The callback raises on its third call; the test checks both that the
    exception propagates and that no later paths are visited.
    """
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

    with pytest.raises(MyError, match=match('custom')):
        Crawler(crawl_directory_path).apply(callback)


def test_apply_on_group_visits_paths_from_both(
    crawl_directory_path: Union[str, Path],
    second_crawl_directory_path: Union[str, Path],
):
    """
    Group `apply()` should visit paths from every child crawler.

    The test combines two different crawlers, records callback inputs, and
    compares the visited set with the union of both child traversals.
    """
    seen: list = []
    group = Crawler(crawl_directory_path) + Crawler(second_crawl_directory_path)
    group.apply(seen.append)
    assert set(seen) == set(Crawler(crawl_directory_path)) | set(Crawler(second_crawl_directory_path))


def test_apply_on_group_deduplicates(crawl_directory_path: Union[str, Path]):
    """
    Group `apply()` should deduplicate overlapping child crawlers.

    The test combines the same crawler twice and verifies the callback receives
    each yielded path once, matching a single crawler traversal.
    """
    seen: list = []
    group = Crawler(crawl_directory_path) + Crawler(crawl_directory_path)
    group.apply(seen.append)
    assert seen == list(Crawler(crawl_directory_path))


def test_apply_on_nested_group_deduplicates(crawl_directory_path: Union[str, Path]):
    """
    Nested group `apply()` should preserve group-level deduplication.

    The test nests duplicate crawlers inside another group and verifies that the
    callback still sees only the single-crawler traversal.
    """
    seen: list = []
    group = Crawler(crawl_directory_path) + (Crawler(crawl_directory_path) + Crawler(crawl_directory_path))
    group.apply(seen.append)
    assert seen == list(Crawler(crawl_directory_path))


def test_apply_on_group_with_cancelled_token(
    crawl_directory_path: Union[str, Path],
    second_crawl_directory_path: Union[str, Path],
):
    """
    A cancelled call-level token should stop group `apply()` completely.

    The test passes an already-cancelled token to a group and verifies that no
    child crawler invokes the callback.
    """
    seen: list = []
    group = Crawler(crawl_directory_path) + Crawler(second_crawl_directory_path)
    group.apply(seen.append, token=SimpleToken(cancelled=True))
    assert seen == []


def test_apply_on_group_respects_child_tokens(
    crawl_directory_path: Union[str, Path],
    second_crawl_directory_path: Union[str, Path],
):
    """
    Group `apply()` should respect cancellation on individual child crawlers.

    The test combines one live crawler with one already-cancelled crawler and
    verifies that only the live crawler contributes callback inputs.
    """
    seen: list = []
    live = Crawler(crawl_directory_path)
    dead = Crawler(second_crawl_directory_path, token=SimpleToken(cancelled=True))
    (live + dead).apply(seen.append)
    assert set(seen) == set(live)


def test_apply_with_multipath_crawler_no_dedup(crawl_directory_path: Union[str, Path]):
    """
    `apply()` should preserve duplicate output from one multipath crawler.

    The test crawls the same base path twice through a single crawler and sorts
    stringified paths so the assertion focuses on duplicate membership.
    """
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
    """
    `apply()` should match iteration behavior for nonexistent base paths.

    The test compares the exception type from iteration and `apply()`. If
    iteration yields no error, it also verifies that no callback input appears.
    """
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
