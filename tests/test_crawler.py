import errno
import os
import stat
import sys
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
from dirstree.crawlers.group import CrawlersGroup

INCOMPATIBLE_OPTIONS_MESSAGE = (
    'The "extensions" and "only_files=False" options are incompatible: '
    'extensions can be applied only when the crawler yields files, '
    'because non-file filesystem entities do not have meaningful file extensions.'
)


def custom_filter(path: Path) -> bool:  # noqa: ARG001
    return True


@pytest.mark.parametrize(
    'freeze_kwargs',
    [
        {},
        {'freeze': False},
        {'freeze': True},
    ],
)
def test_only_files_false_yields_files_and_directories(all_entities_directory_path: Union[str, Path], freeze_kwargs):
    """
    Crawling all entities should include both files and directories.

    The test compares the full result set against a fixture tree containing
    regular files, a nested directory, and files inside that directory.
    """
    base_path = Path(all_entities_directory_path)

    assert set(Crawler(all_entities_directory_path, only_files=False, **freeze_kwargs)) == {
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


@pytest.mark.parametrize(
    'freeze_kwargs',
    [
        {},
        {'freeze': False},
        {'freeze': True},
    ],
)
def test_only_files_false_yields_empty_directories(tmp_path: Path, freeze_kwargs):
    """
    Empty directories should be yielded when all filesystem entities are crawled.

    The test creates an otherwise empty directory and checks that it appears in
    the result of crawling its parent with `only_files=False`.
    """
    empty_folder = tmp_path / 'empty_folder'
    empty_folder.mkdir()

    assert empty_folder in set(Crawler(tmp_path, only_files=False, **freeze_kwargs))


@pytest.mark.parametrize(
    'freeze_kwargs',
    [
        {},
        {'freeze': False},
        {'freeze': True},
    ],
)
def test_only_files_false_yields_hidden_paths(all_entities_directory_path: Union[str, Path], freeze_kwargs):
    """
    Hidden paths should not be filtered out by the all-entity mode.

    The test uses fixture entries whose names start with a dot and verifies that
    both the hidden file and hidden directory are yielded.
    """
    paths = set(Crawler(all_entities_directory_path, only_files=False, **freeze_kwargs))

    assert Path(all_entities_directory_path) / '.hidden_file' in paths
    assert Path(all_entities_directory_path) / '.hidden_folder' in paths


@pytest.mark.parametrize(
    'freeze_kwargs',
    [
        {},
        {'freeze': False},
        {'freeze': True},
    ],
)
def test_only_files_false_does_not_yield_base_path(all_entities_directory_path: Union[str, Path], freeze_kwargs):
    """
    Crawling all entities should not yield the base path itself.

    The test verifies that the root passed to the crawler is absent from the
    yielded paths.
    """
    assert Path(all_entities_directory_path) not in set(Crawler(all_entities_directory_path, only_files=False, **freeze_kwargs))


@pytest.mark.parametrize(
    'freeze_kwargs',
    [
        {},
        {'freeze': False},
        {'freeze': True},
    ],
)
def test_default_mode_stays_file_only(all_entities_directory_path: Union[str, Path], freeze_kwargs):
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
    real_paths = set(Crawler(all_entities_directory_path, **freeze_kwargs))

    assert real_paths == expected_paths
    assert all(path.is_file() for path in real_paths)


@pytest.mark.parametrize(
    'freeze_kwargs',
    [
        {},
        {'freeze': False},
        {'freeze': True},
    ],
)
def test_zero_paths_with_only_files_false_returns_empty_list(freeze_kwargs):
    """
    A crawler without base paths should be empty in all-entity mode.

    The test constructs a zero-path crawler with `only_files=False` and verifies
    that iteration returns an empty list.
    """
    assert list(Crawler(only_files=False, **freeze_kwargs)) == []


@pytest.mark.parametrize(
    'freeze_kwargs',
    [
        {},
        {'freeze': False},
        {'freeze': True},
    ],
)
def test_empty_base_directory_with_only_files_false_returns_empty_list(tmp_path: Path, freeze_kwargs):
    """
    An empty base directory should not yield itself.

    The test crawls an empty temporary directory with `only_files=False` and
    expects no child paths.
    """
    assert list(Crawler(tmp_path, only_files=False, **freeze_kwargs)) == []


@pytest.mark.parametrize(
    'freeze_kwargs',
    [
        {},
        {'freeze': False},
        {'freeze': True},
    ],
)
def test_nonexistent_base_path_with_only_files_false_returns_empty_list(tmp_path: Path, freeze_kwargs):
    """
    A nonexistent base path should yield no results in all-entity mode.

    The test points the crawler at a missing path and verifies that no entries
    are yielded in all-entity mode.
    """
    assert list(Crawler(tmp_path / 'missing', only_files=False, **freeze_kwargs)) == []


@pytest.mark.parametrize(
    'freeze_kwargs',
    [
        {},
        {'freeze': False},
        {'freeze': True},
    ],
)
def test_file_base_path_with_only_files_false_returns_empty_list(tmp_path: Path, freeze_kwargs):
    """
    A file used as the base path should not be yielded as its own child.

    The test creates a file, crawls it as the base path with `only_files=False`,
    and expects no results.
    """
    file_path = tmp_path / 'file.py'
    file_path.write_text('content')

    assert list(Crawler(file_path, only_files=False, **freeze_kwargs)) == []


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
    expected_paths = sorted(str(path) for path in list(Crawler(all_entities_directory_path, only_files=False)) * 2)

    real_paths = sorted(
        str(path) for path in Crawler(all_entities_directory_path, all_entities_directory_path, only_files=False)
    )

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


@pytest.mark.skipif(
    sys.platform == 'win32',
    reason='Symlink creation requires administrator privileges on Windows.',
)
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
    link_directory.symlink_to(real_directory, target_is_directory=True)

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


@pytest.mark.skipif(
    sys.platform == 'win32',
    reason='Symlink creation requires administrator privileges on Windows.',
)
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
    file_link.symlink_to(target_file)
    directory_link.symlink_to(target_directory, target_is_directory=True)
    broken_link.symlink_to(tmp_path / 'missing')

    paths = set(Crawler(tmp_path, only_files=False))

    assert file_link in paths
    assert directory_link in paths
    assert broken_link in paths


@pytest.mark.skipif(
    sys.platform == 'win32' or sys.version_info >= (3, 13),
    reason='Path.rglob does not raise PermissionError for chmod(0) directories on Windows, and on Python 3.13+ pathlib silently skips inaccessible entries.',
)
@pytest.mark.parametrize(
    'freeze_kwargs',
    [
        {},
        {'freeze': False},
        {'freeze': True},
    ],
)
def test_rglob_errors_propagate_with_only_files_false(tmp_path: Path, freeze_kwargs):
    """
    Traversal errors from `Path.rglob` should not be swallowed.

    The test creates an unreadable directory and verifies that the crawler
    propagates the same `PermissionError` that `Path.rglob` would surface.
    """
    blocked = tmp_path / 'blocked'
    blocked.mkdir()
    (blocked / 'file.txt').write_text('content')
    blocked.chmod(0)

    try:
        with pytest.raises(
            PermissionError,
            match=match(str(PermissionError(errno.EACCES, os.strerror(errno.EACCES), str(blocked)))),
        ):
            list(Crawler(tmp_path, only_files=False, **freeze_kwargs))
    finally:
        blocked.chmod(stat.S_IRWXU)


@pytest.mark.skipif(
    sys.platform == 'win32' or sys.version_info < (3, 13),
    reason='pathlib silently swallows OSError during traversal only on POSIX Python 3.13+.',
)
@pytest.mark.parametrize(
    'freeze_kwargs',
    [
        {},
        {'freeze': False},
        {'freeze': True},
    ],
)
def test_unreadable_subdirectory_is_silently_skipped_on_python_3_13_plus_posix(tmp_path: Path, freeze_kwargs):
    """
    On POSIX Python 3.13+, `Path.rglob` deliberately swallows `OSError` (and
    its `PermissionError` subclass) for inaccessible entries to match
    `glob.glob` behaviour. The crawler must transparently inherit that
    contract: an unreadable subdirectory does not raise — it just contributes
    nothing to the result, while the rest of the tree iterates as usual.

    Mirror of `test_rglob_errors_propagate_with_only_files_false`, which is
    skipped on this same combination of platform and Python version because
    the propagation invariant simply does not apply there. Together the two
    tests pin down `Crawler`'s observable behaviour around `OSError` from
    `rglob` across the whole CI matrix.
    """
    visible = tmp_path / 'visible.txt'
    visible.write_text('content')
    blocked = tmp_path / 'blocked'
    blocked.mkdir()
    hidden = blocked / 'hidden.txt'
    hidden.write_text('content')
    blocked.chmod(0)

    try:
        paths = set(Crawler(tmp_path, only_files=False, **freeze_kwargs))
    finally:
        blocked.chmod(stat.S_IRWXU)

    assert visible in paths
    assert blocked in paths
    assert hidden not in paths


@pytest.mark.parametrize(
    'freeze_kwargs',
    [
        {},
        {'freeze': False},
        {'freeze': True},
    ],
)
def test_crawl_test_directory_with_default_extensions(
    crawl_directory_path: Union[str, Path],
    freeze_kwargs,
):
    """
    The default crawler should return every file from the fixture tree.

    The test compares sorted string paths with the full expected file list,
    including files in the nested directory.
    """
    crawler = Crawler(crawl_directory_path, **freeze_kwargs)

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
    """
    Extension filtering should keep only matching files.

    The test crawls the fixture with `extensions=['.txt']` and expects exactly
    the single text file from the nested directory.
    """
    crawler = Crawler(crawl_directory_path, extensions=['.txt'])

    assert [str(x) for x in crawler] == [
        os.path.join(
            'tests', 'test_files', 'walk_it', 'nested_folder', 'non_python_file.txt',
        ),
    ]


@pytest.mark.parametrize(
    'freeze_kwargs',
    [
        {},
        {'freeze': False},
        {'freeze': True},
    ],
)
def test_crawl_test_directory_with_py_extension(crawl_directory_path: Union[str, Path], freeze_kwargs):
    """
    Python extension filtering should keep only `.py` files.

    The test compares sorted paths from `extensions=['.py']` with the expected
    Python files in the fixture tree.
    """
    crawler = Crawler(crawl_directory_path, extensions=['.py'], **freeze_kwargs)

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
    """
    Exclude patterns and extension filtering should compose.

    The test keeps Python files while excluding `__init__.py`, leaving only the
    non-init Python files from the fixture.
    """
    crawler = Crawler(crawl_directory_path, exclude=['__init__.py'], extensions=['.py'])

    assert [str(x) for x in crawler] == [
        os.path.join('tests', 'test_files', 'walk_it', 'simple_code.py'),
        os.path.join(
            'tests', 'test_files', 'walk_it', 'nested_folder', 'python_file.py',
        ),
    ]


@pytest.mark.parametrize(
    'freeze_kwargs',
    [
        {},
        {'freeze': False},
        {'freeze': True},
    ],
)
def test_crawl_test_directory_with_exclude_patterns_without_extensions(
    crawl_directory_path: Union[str, Path],
    freeze_kwargs,
):
    """
    Exclude patterns should apply when no extension filter is configured.

    The test excludes `__init__.py` and verifies that all other fixture files
    remain in the result.
    """
    crawler = Crawler(crawl_directory_path, exclude=['__init__.py'], **freeze_kwargs)

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


@pytest.mark.parametrize(
    'freeze_kwargs',
    [
        {},
        {'freeze': False},
        {'freeze': True},
    ],
)
def test_crawl_test_directory_with_exclude_patterns_and_extensions(
    crawl_directory_path: Union[str, Path],
    freeze_kwargs,
):
    """
    Exclude patterns should compose with non-Python extension filtering.

    The test combines `extensions=['.txt']` with an unrelated init-file exclude
    and verifies that the text file is still yielded.
    """
    crawler = Crawler(
        crawl_directory_path, extensions=['.txt'], exclude=['__init__.py'], **freeze_kwargs,
    )

    assert [str(x) for x in crawler] == [
        os.path.join(
            'tests', 'test_files', 'walk_it', 'nested_folder', 'non_python_file.txt',
        ),
    ]


def test_repr():
    """
    Crawler and group representations should include configured options.

    The assertions cover plain crawlers, filters, tokens, excludes, extensions,
    mixed crawler groups, and every `freeze` combination (alone, with each
    other option, hidden when explicitly `False`, and shown last after
    `only_files`). For `freeze=True` we also verify that `PythonCrawler` keeps
    its hardcoded `extensions` hidden while still exposing the new field.
    """
    assert repr(Crawler('.')) == "Crawler('.')"
    assert repr(Crawler('usr/bin')) == "Crawler('usr/bin')"
    assert repr(Crawler('.', extensions=['.py'])) == "Crawler('.', extensions=['.py'])"
    assert repr(Crawler('.', exclude=['*.py'], extensions=['.py'])) == "Crawler('.', extensions=['.py'], exclude=['*.py'])"
    assert repr(Crawler('.', exclude=['*.py'])) == "Crawler('.', exclude=['*.py'])"
    assert repr(Crawler('.', filter=custom_filter)) == "Crawler('.', filter=custom_filter)"
    assert repr(Crawler('.', filter=lambda x: True)) == "Crawler('.', filter=lambda x: True)"  # noqa: ARG005
    assert repr(Crawler('.', token=ConditionToken(lambda: True))) == "Crawler('.', token=ConditionToken(λ))"
    assert repr(Crawler('../dirstree') + Crawler('../cantok')) == "CrawlersGroup([Crawler('../dirstree'), Crawler('../cantok')])"
    assert repr(Crawler('../dirstree') + PythonCrawler('../cantok')) == "CrawlersGroup([Crawler('../dirstree'), PythonCrawler('../cantok')])"

    assert repr(Crawler('.', freeze=True)) == "Crawler('.', freeze=True)"
    assert repr(Crawler('.', freeze=False)) == "Crawler('.')"
    assert repr(Crawler('.', extensions=['.py'], freeze=True)) == "Crawler('.', extensions=['.py'], freeze=True)"
    assert repr(Crawler('.', only_files=False, freeze=True)) == "Crawler('.', only_files=False, freeze=True)"
    assert repr(Crawler('.', filter=custom_filter, freeze=True)) == "Crawler('.', filter=custom_filter, freeze=True)"
    assert repr(Crawler('.', token=ConditionToken(lambda: True), freeze=True)) == "Crawler('.', token=ConditionToken(λ), freeze=True)"
    assert repr(PythonCrawler('.', freeze=True)) == "PythonCrawler('.', freeze=True)"


@pytest.mark.parametrize(
    'freeze_kwargs',
    [
        {},
        {'freeze': False},
        {'freeze': True},
    ],
)
@pytest.mark.parametrize(
    'factory',
    [
        Crawler,
        PythonCrawler,
    ],
)
def test_iter(factory: Type[Crawler], freeze_kwargs):
    """
    Iterating a crawler should delegate to `go()`.

    The test runs both crawler classes and compares `list(crawler)` with
    `list(crawler.go())`.
    """
    crawler = factory('.', **freeze_kwargs)

    assert list(crawler) == list(crawler.go())


@pytest.mark.parametrize(
    'freeze_kwargs',
    [
        {},
        {'freeze': False},
        {'freeze': True},
    ],
)
@pytest.mark.parametrize(
    'factory',
    [
        Crawler,
        PythonCrawler,
    ],
)
def test_crawl_repeat(factory: Type[Crawler], freeze_kwargs):
    """
    Crawlers should be reusable across repeated iterations.

    The test materializes the same crawler twice and verifies that both
    iterations produce identical results.
    """
    crawler = factory('.', **freeze_kwargs)

    assert list(crawler) == list(crawler)


@pytest.mark.parametrize(
    'freeze_kwargs',
    [
        {},
        {'freeze': False},
        {'freeze': True},
    ],
)
@pytest.mark.parametrize(
    'factory',
    [
        Crawler,
        PythonCrawler,
    ],
)
def test_filter_skips_first_path(factory: Type[Crawler], freeze_kwargs):
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

    assert list(factory('.', **freeze_kwargs))[1:] == list(factory('.', filter=empty_filter, **freeze_kwargs))


@pytest.mark.parametrize(
    'freeze_kwargs',
    [
        {},
        {'freeze': False},
        {'freeze': True},
    ],
)
@pytest.mark.parametrize(
    'factory',
    [
        Crawler,
        PythonCrawler,
    ],
)
def test_argument_of_filter_is_path_object(crawl_directory_path: Union[str, Path], factory: Type[Crawler], freeze_kwargs):
    """
    Filters should receive the same `Path` objects that traversal yields.

    The test records every filter argument and compares that collector with the
    crawler's yielded result for both crawler classes.
    """
    collector = []

    def empty_filter(path):
        collector.append(path)
        return True

    crawler = factory(crawl_directory_path, filter=empty_filter, **freeze_kwargs)

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
    'freeze_kwargs',
    [
        {},
        {'freeze': False},
        {'freeze': True},
    ],
)
@pytest.mark.parametrize(
    'factory',
    [
        Crawler,
        PythonCrawler,
    ],
)
def test_cancelled_token(crawl_directory_path: Union[str, Path], factory: Type[Crawler], freeze_kwargs):
    """
    An already-cancelled token should suppress traversal.

    The test passes a cancelled token to both crawler classes and expects an
    empty result.
    """
    assert list(factory(crawl_directory_path, token=SimpleToken(cancelled=True), **freeze_kwargs)) == []


@pytest.mark.parametrize(
    'freeze_kwargs',
    [
        {},
        {'freeze': False},
        {'freeze': True},
    ],
)
@pytest.mark.parametrize(
    'factory',
    [
        Crawler,
        PythonCrawler,
    ],
)
def test_default_token(crawl_directory_path: Union[str, Path], factory: Type[Crawler], freeze_kwargs):
    """
    An explicit default token should behave like the implicit default.

    The test compares traversal with `DefaultToken()` to normal traversal for
    both crawler classes.
    """
    assert list(factory(crawl_directory_path, token=DefaultToken(), **freeze_kwargs)) == list(
        factory(crawl_directory_path, **freeze_kwargs),
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
    """
    A group made from duplicate crawlers should deduplicate paths.

    The test adds two equivalent crawlers and compares the group result with a
    single crawler traversal.
    """
    assert list(Crawler(crawl_directory_path) + Crawler(crawl_directory_path)) == list(Crawler(crawl_directory_path))


def test_deduplication_with_sum_of_crawlers_and_group(crawl_directory_path: Union[str, Path]):
    """
    Deduplication should also work through nested crawler groups.

    The test nests a duplicate group inside another addition and expects the
    same result as a single crawler.
    """
    assert list(Crawler(crawl_directory_path) + (Crawler(crawl_directory_path) + Crawler(crawl_directory_path))) == list(Crawler(crawl_directory_path))


def test_sum_of_crawlers(crawl_directory_path: Union[str, Path]):
    """
    Adding crawlers with complementary extension filters should cover all files.

    The test combines `.py` and `.txt` crawlers and compares the sorted group
    result with the default crawler over the same fixture.
    """
    first_crawler = Crawler(crawl_directory_path, extensions=['.py'])
    second_crawler = Crawler(crawl_directory_path, extensions=['.txt'])

    supercrawler = first_crawler + second_crawler

    supercrawlers_result = list(supercrawler)
    simplecrawlers_result = list(Crawler(crawl_directory_path))

    supercrawlers_result.sort()
    simplecrawlers_result.sort()

    assert supercrawlers_result == simplecrawlers_result


def test_sum_usual_crawler_and_python_crawler():
    """
    Mixed filters should combine to the same result as an unrestricted crawler.

    The test adds a Python-file crawler to a crawler filtering out Python files,
    then compares the sorted result with the default current-directory crawl.
    """
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


@pytest.mark.parametrize(
    'freeze_kwargs',
    [
        {},
        {'freeze': False},
        {'freeze': True},
    ],
)
def test_crawl_two_folders(crawl_directory_path: Union[str, Path], second_crawl_directory_path: Union[str, Path], freeze_kwargs):
    """
    A multipath crawler should traverse base paths in argument order.

    The test compares one crawler with two base paths to the concatenation of
    two single-path crawler results.
    """
    assert list(Crawler(crawl_directory_path, second_crawl_directory_path, **freeze_kwargs)) == list(Crawler(crawl_directory_path, **freeze_kwargs)) + list(Crawler(second_crawl_directory_path, **freeze_kwargs))


def test_crawl_without_path():
    """
    A crawler without base paths should yield nothing.

    The test constructs `Crawler()` and expects an empty list.
    """
    assert list(Crawler()) == []


def test_check_filter_signature():
    """
    Filter callables should accept exactly the expected path argument.

    The test passes a zero-argument callable and verifies the signature
    validation error.
    """
    with pytest.raises(SignatureMismatchError, match=match('The signature of the callable object does not match the expected one.')):
        Crawler(filter=lambda: None)


def test_apply_calls_function_once_per_file(crawl_directory_path: Union[str, Path]):
    """
    `apply()` should call the callback once for every yielded file.

    The test counts callback inputs and compares that count with normal
    iteration length.
    """
    seen: list = []

    Crawler(crawl_directory_path).apply(seen.append)

    assert len(seen) == len(list(Crawler(crawl_directory_path)))


def test_apply_passes_path_instance(crawl_directory_path: Union[str, Path]):
    """
    `apply()` should pass `Path` objects to callbacks.

    The test records `isinstance(path, Path)` for every callback input and
    requires all callback arguments to be paths.
    """
    types_seen: list = []

    Crawler(crawl_directory_path).apply(lambda p: types_seen.append(isinstance(p, Path)))

    assert types_seen
    assert all(types_seen)


def test_apply_set_matches_iteration_set(crawl_directory_path: Union[str, Path]):
    """
    `apply()` should visit the same path set as iteration.

    The test collects callback inputs and compares their set with the crawler's
    iteration set.
    """
    seen: list = []

    Crawler(crawl_directory_path).apply(seen.append)

    assert set(seen) == set(Crawler(crawl_directory_path))


def test_apply_order_matches_iteration(crawl_directory_path: Union[str, Path]):
    """
    `apply()` should preserve iteration order.

    The test records callback inputs and compares the list directly with normal
    crawler iteration.
    """
    seen: list = []

    Crawler(crawl_directory_path).apply(seen.append)

    assert seen == list(Crawler(crawl_directory_path))


def test_apply_returns_none(crawl_directory_path: Union[str, Path]):
    """
    `apply()` should be a side-effect API that returns `None`.

    The test calls `apply()` with a callback that returns its input and verifies
    that `apply()` itself still returns `None`.
    """
    assert Crawler(crawl_directory_path).apply(lambda x: x) is None  # type: ignore[func-returns-value]


def test_apply_multiple_invocations_independent(crawl_directory_path: Union[str, Path]):
    """
    Multiple `apply()` calls on one crawler should be independent.

    The test runs `apply()` twice, records both callback sequences, and compares
    them with each other and with normal iteration.
    """
    crawler = Crawler(crawl_directory_path)
    first: list = []
    second: list = []

    crawler.apply(first.append)
    crawler.apply(second.append)

    assert first == second
    assert first == list(crawler)


@pytest.mark.parametrize(
    'freeze_kwargs',
    [
        {},
        {'freeze': False},
        {'freeze': True},
    ],
)
def test_apply_on_empty_directory(tmp_path: Path, freeze_kwargs):
    """
    `apply()` should not call the callback for an empty directory.

    The test crawls an empty temporary directory and verifies that the collector
    remains empty.
    """
    seen: list = []

    Crawler(tmp_path, **freeze_kwargs).apply(seen.append)

    assert seen == []


def test_apply_respects_extensions(crawl_directory_path: Union[str, Path]):
    """
    `apply()` should respect extension filters.

    The test applies a `.py` filter, records callback inputs, and verifies that
    every visited path has a Python suffix.
    """
    seen: list = []

    Crawler(crawl_directory_path, extensions=['.py']).apply(seen.append)

    assert seen
    assert all(p.suffix == '.py' for p in seen)


def test_apply_respects_exclude(crawl_directory_path: Union[str, Path]):
    """
    `apply()` should respect exclude patterns.

    The test excludes `__init__.py`, records callback inputs, and verifies that
    none of the visited paths have that file name.
    """
    seen: list = []

    Crawler(crawl_directory_path, exclude=['__init__.py']).apply(seen.append)

    assert seen
    assert all(p.name != '__init__.py' for p in seen)


def test_apply_respects_custom_filter(crawl_directory_path: Union[str, Path]):
    """
    `apply()` should respect the custom filter callable.

    The test keeps only Python files via `filter`, records callback inputs, and
    verifies that every visited path matches that predicate.
    """
    seen: list = []

    Crawler(crawl_directory_path, filter=lambda x: x.suffix == '.py').apply(seen.append)

    assert seen
    assert all(p.suffix == '.py' for p in seen)


def test_apply_respects_all_filters_combined(crawl_directory_path: Union[str, Path]):
    """
    `apply()` should use the same combined filtering rules as iteration.

    The test combines extensions, excludes, and a custom filter, then compares
    callback inputs with normal iteration for the same crawler options.
    """
    seen: list = []
    kwargs = dict(
        extensions=['.py'],
        exclude=['__init__.py'],
        filter=lambda x: 'simple' in x.name,
    )

    Crawler(crawl_directory_path, **kwargs).apply(seen.append)  # type: ignore[arg-type]

    assert seen == list(Crawler(crawl_directory_path, **kwargs))  # type: ignore[arg-type]


def test_apply_with_cancelled_call_time_token_skips_callback(crawl_directory_path: Union[str, Path]):
    """
    A cancelled call-level token should suppress `apply()` callbacks.

    The test passes an already-cancelled token to `apply()` and verifies that no
    path reaches the callback.
    """
    seen: list = []

    Crawler(crawl_directory_path).apply(seen.append, token=SimpleToken(cancelled=True))

    assert seen == []


def test_apply_with_cancelled_instance_token_skips_callback(crawl_directory_path: Union[str, Path]):
    """
    A cancelled instance token should suppress `apply()` callbacks.

    The test creates a crawler with an already-cancelled token and verifies that
    `apply()` never calls the callback.
    """
    seen: list = []

    Crawler(crawl_directory_path, token=SimpleToken(cancelled=True)).apply(seen.append)

    assert seen == []


@pytest.mark.parametrize('n', [0, 1, 2, 3])
def test_apply_with_condition_token_cancels_after_n(crawl_directory_path: Union[str, Path], n: int):
    """
    A condition token passed to `apply()` should stop after the expected prefix.

    The callback increments a counter, the token cancels when it reaches `n`,
    and the visited paths are compared with the first `n` iteration results.
    """
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
    """
    `apply()` should require both instance and call tokens to allow traversal.

    The parametrized cases cover every combination where at least one token is
    already cancelled, and all of them should skip callbacks.
    """
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


@pytest.mark.parametrize(
    'freeze_kwargs',
    [
        {},
        {'freeze': False},
        {'freeze': True},
    ],
)
def test_apply_with_zero_arg_callable_raises(crawl_directory_path: Union[str, Path], freeze_kwargs):
    """
    `apply()` should reject callbacks without a path argument.

    The test gives the crawler a filter with a side effect, passes a
    zero-argument callback to `apply()`, and verifies both the signature
    validation error and that traversal never reached the filter.
    """
    filter_calls: list = []

    def collect_filter(path: Path) -> bool:
        filter_calls.append(path)
        return True

    with pytest.raises(SignatureMismatchError, match=match('The signature of the callable object does not match the expected one.')):
        Crawler(crawl_directory_path, filter=collect_filter, **freeze_kwargs).apply(lambda: None)  # type: ignore[misc, arg-type]

    assert filter_calls == []


@pytest.mark.parametrize(
    'freeze_kwargs',
    [
        {},
        {'freeze': False},
        {'freeze': True},
    ],
)
def test_apply_with_two_arg_callable_raises(crawl_directory_path: Union[str, Path], freeze_kwargs):
    """
    `apply()` should reject callbacks requiring too many positional arguments.

    The test passes a two-argument callable and checks the exact signature
    validation message.
    """
    with pytest.raises(
        SignatureMismatchError,
        match=match(
            'This is a difficult situation, there is no guarantee that a call with a variable number of positional arguments will fill all the slots of positional arguments.',
        ),
    ):
        Crawler(crawl_directory_path, **freeze_kwargs).apply(lambda x, y: None)  # type: ignore[misc, arg-type]  # noqa: ARG005


def test_apply_with_def_function_works(crawl_directory_path: Union[str, Path]):
    """
    `apply()` should accept a normal one-argument function.

    The test records every path received by a nested function callback and
    compares it with normal iteration.
    """
    seen: list = []

    def callback(path: Path) -> None:
        seen.append(path)

    Crawler(crawl_directory_path).apply(callback)

    assert seen == list(Crawler(crawl_directory_path))


def test_apply_validation_runs_at_apply_not_construction(crawl_directory_path: Union[str, Path]):
    """
    `apply()` should validate the callback when it is called.

    The test constructs a crawler successfully, then passes an invalid callback
    to `apply()` and expects the signature error there.
    """
    crawler = Crawler(crawl_directory_path)

    with pytest.raises(SignatureMismatchError, match=match('The signature of the callable object does not match the expected one.')):
        crawler.apply(lambda: None)  # type: ignore[misc, arg-type]


def test_apply_with_callable_class_instance(crawl_directory_path: Union[str, Path]):
    """
    `apply()` should accept callable objects.

    The test uses an instance with `__call__`, records its `seen` paths, and
    compares them with normal iteration.
    """
    class Recorder:
        def __init__(self) -> None:
            self.seen: list = []

        def __call__(self, path: Path) -> None:
            self.seen.append(path)

    recorder = Recorder()

    Crawler(crawl_directory_path).apply(recorder)

    assert recorder.seen == list(Crawler(crawl_directory_path))


def test_apply_with_functools_partial(crawl_directory_path: Union[str, Path]):
    """
    `apply()` should accept partially-applied callables.

    The test binds a prefix argument with `functools.partial` and verifies that
    each callback call receives the expected path as the remaining argument.
    """
    seen: list = []

    def cb(prefix: str, path: Path) -> None:
        seen.append((prefix, path))

    Crawler(crawl_directory_path).apply(partial(cb, 'hit'))

    expected = list(Crawler(crawl_directory_path))

    assert seen == [('hit', p) for p in expected]


def test_apply_with_bound_method(crawl_directory_path: Union[str, Path]):
    """
    `apply()` should accept bound methods.

    The test passes an instance method as the callback and verifies that the
    instance records the same paths as normal iteration.
    """
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
    """
    `apply()` should propagate exceptions raised by the callback.

    The test uses a callback that always raises `ValueError` and checks the
    exact propagated message.
    """
    def boom(path: Path) -> None:  # noqa: ARG001
        raise ValueError('boom')

    with pytest.raises(ValueError, match=match('boom')):
        Crawler(crawl_directory_path).apply(boom)


@pytest.mark.parametrize(
    'freeze_kwargs',
    [
        {},
        {'freeze': False},
        {'freeze': True},
    ],
)
def test_apply_stops_iteration_on_first_exception(crawl_directory_path: Union[str, Path], freeze_kwargs):
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
        Crawler(crawl_directory_path, **freeze_kwargs).apply(callback)

    assert counter == 3


def test_apply_preserves_custom_exception_type(crawl_directory_path: Union[str, Path]):
    """
    `apply()` should preserve custom exception types from callbacks.

    The test raises a local exception class from the callback and verifies that
    the same type and message escape.
    """
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
    """
    `apply()` should reject non-callable callback objects.

    The test passes `None` and verifies the exact `ValueError` raised by
    signature detection.
    """
    with pytest.raises(ValueError, match=match('It is impossible to determine the signature of an object that is not being callable.')):
        Crawler(crawl_directory_path).apply(None)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    'freeze_kwargs',
    [
        {},
        {'freeze': False},
        {'freeze': True},
    ],
)
def test_apply_on_zero_path_crawler_never_calls_callback(freeze_kwargs):
    """
    `apply()` on a zero-path crawler should be a no-op.

    The test applies a collector callback to `Crawler()` and verifies that no
    callback input is recorded.
    """
    seen: list = []

    Crawler(**freeze_kwargs).apply(seen.append)

    assert seen == []


@pytest.mark.parametrize(
    'freeze_kwargs',
    [
        {},
        {'freeze': False},
        {'freeze': True},
    ],
)
def test_apply_on_nonexistent_base_path_matches_iteration_behavior(tmp_path: Path, freeze_kwargs):
    """
    `apply()` should match iteration behavior for nonexistent base paths.

    The test records the normal iteration result for a missing path and verifies
    that `apply()` visits exactly the same paths.
    """
    nonexistent = tmp_path / 'does_not_exist'

    iter_paths = list(Crawler(nonexistent, **freeze_kwargs))
    seen: list = []

    Crawler(nonexistent, **freeze_kwargs).apply(seen.append)

    assert seen == iter_paths


@pytest.mark.skipif(
    sys.platform == 'win32' or sys.version_info >= (3, 13),
    reason='Path.rglob does not raise PermissionError for chmod(0) directories on Windows, and on Python 3.13+ pathlib silently skips inaccessible entries.',
)
def test_apply_propagates_rglob_errors_with_only_files_false(tmp_path: Path):
    """
    `apply()` should propagate traversal errors from all-entity crawling.

    The test creates an unreadable directory and verifies that `apply()` via the
    crawler surfaces the same `PermissionError` that direct `rglob` would raise.
    """
    blocked = tmp_path / 'blocked'
    blocked.mkdir()
    (blocked / 'file.txt').write_text('content')
    blocked.chmod(0)
    seen: list = []

    try:
        with pytest.raises(
            PermissionError,
            match=match(str(PermissionError(errno.EACCES, os.strerror(errno.EACCES), str(blocked)))),
        ):
            Crawler(tmp_path, only_files=False).apply(seen.append)
    finally:
        blocked.chmod(stat.S_IRWXU)


@pytest.mark.parametrize(
    'freeze_kwargs',
    [
        {},
        {'freeze': False},
        {'freeze': True},
    ],
)
def test_apply_on_file_base_path_matches_iteration_behavior(tmp_path: Path, freeze_kwargs):
    """
    `apply()` should match iteration when the base path is a file.

    The test creates a file base path, records iteration output, then verifies
    that `apply()` visits exactly the same paths.
    """
    file_path = tmp_path / 'a_file.txt'
    file_path.write_text('hi')

    iter_paths = list(Crawler(file_path, **freeze_kwargs))
    seen: list = []

    Crawler(file_path, **freeze_kwargs).apply(seen.append)

    assert seen == iter_paths


@pytest.mark.parametrize(
    'factory',
    [
        Crawler,
        PythonCrawler,
    ],
)
def test_freeze_default_is_false(factory: Type[Crawler]):
    """
    The new `freeze` option should default to `False` for every crawler class.

    The test guards backward compatibility: code that does not mention `freeze`
    must observe `crawler.frozen is False`. The internal attribute is named
    `frozen` (state form) while the external parameter is the verb `freeze`.
    """
    assert factory('.').frozen is False


@pytest.mark.parametrize(
    'factory',
    [
        Crawler,
        PythonCrawler,
    ],
)
def test_freeze_is_keyword_only(factory: Type[Crawler]):
    """
    The new `freeze` option should refuse positional arguments.

    The test inspects the public constructor signature and verifies that the
    parameter is keyword-only for both crawler classes.
    """
    assert signature(factory).parameters['freeze'].kind is Parameter.KEYWORD_ONLY


@pytest.mark.parametrize(
    'factory',
    [
        Crawler,
        PythonCrawler,
    ],
)
@pytest.mark.parametrize(
    'value',
    [
        True,
        False,
    ],
)
def test_freeze_value_is_stored_on_instance(factory: Type[Crawler], value: bool):
    """
    An explicit `freeze=value` should be reflected by the `.frozen` attribute.

    The test runs over both crawler classes and both boolean values. It guards
    against bugs like `self.frozen = freeze or False` (which would lose `True`
    silently for the wrong branch) and against PythonCrawler regressions in
    `super().__init__` plumbing. It also locks down the naming convention
    (external `freeze` keyword → internal `frozen` attribute).
    """
    assert factory('.', freeze=value).frozen is value


def test_freeze_filter_called_for_all_paths_during_snapshot_construction_before_first_yield(tmp_path: Path):
    """
    With `freeze=True`, the user filter is invoked for every candidate path
    during snapshot construction, before the iterator yields anything.

    This is the primary observable evidence of "snapshot is built before
    iteration begins". A tracking filter records every path it sees; after the
    very first `next()` on the iterator, the recorded set already equals the
    full set of files in the directory, even though only one yield has
    happened. This precludes the lazy interpretation where the filter would be
    called incrementally with each yield.
    """
    files = sorted(tmp_path / f'f{i}.txt' for i in range(5))
    for path in files:
        path.touch()

    seen: List[Path] = []

    def tracking(path: Path) -> bool:
        seen.append(path)
        return True

    iterator = iter(Crawler(tmp_path, freeze=True, filter=tracking))
    first = next(iterator)

    assert set(seen) == set(files)
    assert first in set(files)


def test_freeze_filter_not_called_again_during_remaining_iteration(tmp_path: Path):
    """
    Once the snapshot has been materialised during the first `next()`, the
    user filter is not invoked again while the remaining snapshot is yielded.

    The test complements C1: the snapshot is built exactly once and reused for
    the rest of the iteration. We freeze the filter-call count immediately
    after the first `next()`, drain the iterator, and verify the count has not
    increased.
    """
    files = [tmp_path / f'f{i}.txt' for i in range(5)]
    for path in files:
        path.touch()

    seen: List[Path] = []

    def tracking(path: Path) -> bool:
        seen.append(path)
        return True

    iterator = iter(Crawler(tmp_path, freeze=True, filter=tracking))
    next(iterator)
    count_after_first = len(seen)

    list(iterator)

    assert len(seen) == count_after_first


def test_without_freeze_filter_is_called_lazily(tmp_path: Path):
    """
    Without `freeze=True`, the filter — which always returns `True` — is
    invoked lazily so that after the first `next()` only a strict subset of
    paths has been seen.

    The contrast with C1 shows that the timing difference is exactly what
    `freeze=True` changes. The filter is intentionally always-`True` so that
    `len(seen) < N` cannot accidentally hold for some other reason (e.g.
    selective filtering). The strict bound `0 < len(seen) < N` proves the
    iteration is incremental.
    """
    files = [tmp_path / f'f{i}.txt' for i in range(5)]
    for path in files:
        path.touch()

    seen: List[Path] = []

    def tracking(path: Path) -> bool:
        seen.append(path)
        return True

    iterator = iter(Crawler(tmp_path, filter=tracking))
    next(iterator)

    assert 0 < len(seen) < len(files)


def test_freeze_apply_processes_all_files_even_when_callback_deletes_them(tmp_path: Path):
    """
    `apply()` with `freeze=True` should call the callback for every snapshot
    path, even when the callback deletes files mid-iteration.

    This is the primary user-facing scenario from the spec: walk a directory
    and remove each file. Without `freeze` the result is filesystem-dependent
    because deletion races with `rglob`. With `freeze` the snapshot is built
    up front, so the callback runs for every captured path regardless of how
    it mutates the directory.
    """
    files = [tmp_path / f'f{i}.txt' for i in range(7)]
    for path in files:
        path.touch()

    processed: List[Path] = []

    def delete_callback(path: Path) -> None:
        processed.append(path)
        path.unlink()

    Crawler(tmp_path, freeze=True).apply(delete_callback)

    assert set(processed) == set(files)
    for path in files:
        assert not path.exists()


def test_freeze_does_not_yield_files_created_after_snapshot(tmp_path: Path):
    """
    A file created between snapshot construction and the end of iteration
    should not be yielded.

    The complement of C4 — the snapshot does not pick up new files that arrive
    after construction. The test creates two files, starts iteration to force
    snapshot materialisation, then creates a third file before draining the
    rest; the yielded set is exactly the two original files.
    """
    file1 = tmp_path / 'a.txt'
    file2 = tmp_path / 'b.txt'
    file1.touch()
    file2.touch()

    iterator = iter(Crawler(tmp_path, freeze=True))
    first = next(iterator)

    late = tmp_path / 'late.txt'
    late.touch()

    rest = list(iterator)

    yielded = {first, *rest}

    assert yielded == {file1, file2}
    assert late not in yielded


@pytest.mark.parametrize(
    'mutate',
    [
        lambda p: p.rename(p.parent / 'renamed.txt'),
        lambda p: p.unlink(),
    ],
    ids=['rename', 'delete'],
)
def test_freeze_yields_snapshot_path_after_rename_or_delete(tmp_path: Path, mutate):
    """
    Paths captured into the snapshot are yielded as-is regardless of whether
    the underlying file is renamed or deleted after the snapshot has been built.

    The two `mutate` variants exercise inode-rename (reuse under a different
    name) and outright deletion. In both cases the snapshot still yields the
    original `Path` object — proving that the snapshot stores plain `Path`
    values, not live filesystem references.

    The test creates exactly two files and consumes the first one to force
    snapshot construction. It then asserts that exactly one file remains in
    the snapshot (defensively rejecting the case where snapshot collapsed to a
    single entry), mutates the remaining file, and confirms that the second
    `next()` yields its original (pre-mutation) path.
    """
    file1 = tmp_path / 'a.txt'
    file2 = tmp_path / 'b.txt'
    file1.touch()
    file2.touch()

    iterator = iter(Crawler(tmp_path, freeze=True))
    first = next(iterator)

    expected_other_set = {file1, file2} - {first}
    assert len(expected_other_set) == 1
    other = next(iter(expected_other_set))

    mutate(other)

    assert next(iterator) == other

    with pytest.raises(StopIteration):
        next(iterator)


@pytest.mark.skipif(
    sys.platform == 'win32',
    reason='file→directory replacement at the same path while a Path object is alive is unreliable on Windows.',
)
def test_freeze_yields_snapshot_path_after_replace_with_directory(tmp_path: Path):
    """
    A path captured as a file in the snapshot must still be yielded after the
    file has been unlinked and replaced by a directory of the same name.

    This is a third family of post-snapshot mutation — node-type change — that
    complements rename and delete. The snapshot stores plain `Path` values, so
    the on-disk identity of the path is irrelevant to what the iterator emits.
    """
    file1 = tmp_path / 'a.txt'
    file2 = tmp_path / 'b.txt'
    file1.touch()
    file2.touch()

    iterator = iter(Crawler(tmp_path, freeze=True))
    first = next(iterator)

    expected_other_set = {file1, file2} - {first}
    assert len(expected_other_set) == 1
    other = next(iter(expected_other_set))

    other.unlink()
    other.mkdir()

    assert next(iterator) == other

    with pytest.raises(StopIteration):
        next(iterator)


def test_freeze_token_cancelling_exactly_after_snapshot_built_yields_nothing(tmp_path: Path):
    """
    A token that becomes cancelled exactly when snapshot construction
    completes — before any path is yielded — should produce zero items, yet
    the snapshot itself must have been built completely.

    The test closes the race window between the end of
    `list(self._traverse(token))` and the first iteration of
    `for path in snapshot:`. We use a counting filter and a ConditionToken
    that flips to cancelled once the filter has been called for every file
    (i.e. once the snapshot is about to close). The token-check in `go()`
    before the first yield sees the cancellation and skips the entire yield
    loop, while `len(seen) == N` confirms the snapshot was fully constructed
    first.
    """
    files = [tmp_path / f'f{i}.txt' for i in range(3)]
    for path in files:
        path.touch()

    seen: List[Path] = []

    def tracking(path: Path) -> bool:
        seen.append(path)
        return True

    token = ConditionToken(lambda: len(seen) >= len(files))
    crawler = Crawler(tmp_path, freeze=True, filter=tracking, token=token)

    assert list(crawler) == []
    assert len(seen) == len(files)


@pytest.mark.parametrize(
    'token_route',
    [
        'instance',
        'call',
    ],
)
def test_freeze_with_already_cancelled_token_yields_nothing_and_skips_filter(tmp_path: Path, token_route: str):
    """
    An already-cancelled token should suppress both snapshot construction and
    yields, regardless of whether the token is passed to the constructor or
    to `go(token=...)`.

    `go()` begins with `token = token + self.token`, so the two routes are
    instrumentally equivalent. The test parametrises over both routes and
    verifies that no path is yielded and that the user filter is never
    invoked (the snapshot construction does not enter `rglob` when the
    combined token is already cancelled).
    """
    for index in range(3):
        (tmp_path / f'f{index}.txt').touch()

    seen: List[Path] = []

    def tracking(path: Path) -> bool:
        seen.append(path)
        return True

    cancelled = SimpleToken(cancelled=True)
    if token_route == 'instance':
        crawler = Crawler(tmp_path, freeze=True, token=cancelled, filter=tracking)
        result = list(crawler)
    else:
        crawler = Crawler(tmp_path, freeze=True, filter=tracking)
        result = list(crawler.go(cancelled))

    assert result == []
    assert seen == []


@pytest.mark.parametrize(
    'partial_snapshot_size',
    [
        0,
        1,
        2,
        3,
    ],
)
def test_freeze_with_condition_token_cancelling_mid_snapshot_yields_nothing_but_partial_snapshot_built(tmp_path: Path, partial_snapshot_size: int):
    """
    A `ConditionToken` that cancels after the configured number of filter
    calls should truncate the snapshot at that many entries during
    construction, yet the snapshot-yield loop in `go()` yields zero items.

    Important semantic note: cantok tokens are terminal — once cancelled they
    remain cancelled. So although `_traverse` stops appending after the
    configured number of filter calls (snapshot has that length), the
    freeze-branch `for path in snapshot: if not token: break` sees the
    still-cancelled token and exits immediately. The lazy-mode test
    `test_cancel_after_n_iterations` deliberately yields the first
    `partial_snapshot_size` entries, which is not achievable under
    `freeze=True` with the same token construction. The
    truncation-during-build is instead asserted indirectly via
    `len(seen) == partial_snapshot_size`.
    """
    files = [tmp_path / f'f{index}.txt' for index in range(5)]
    for path in files:
        path.touch()

    seen: List[Path] = []

    def tracking(path: Path) -> bool:
        seen.append(path)
        return True

    token = ConditionToken(lambda: len(seen) >= partial_snapshot_size)
    crawler = Crawler(tmp_path, freeze=True, filter=tracking, token=token)

    assert list(crawler) == []
    assert len(seen) == partial_snapshot_size


@pytest.mark.parametrize(
    'factory',
    [
        Crawler,
        PythonCrawler,
    ],
)
def test_freeze_apply_visits_every_snapshot_path(crawl_directory_path: Union[str, Path], factory: Type[Crawler]):
    """
    `apply()` under `freeze=True` should call the callback exactly once for
    every path the crawler would yield.

    The test confirms that `apply()` uses the same yield path as `go()` and
    that this still holds in freeze mode. The callback simply records each
    visited path; the result is compared with direct iteration over the same
    frozen crawler.
    """
    seen: List[Path] = []

    factory(crawl_directory_path, freeze=True).apply(seen.append)

    assert seen == list(factory(crawl_directory_path, freeze=True))


def test_freeze_apply_does_not_visit_files_created_by_callback(tmp_path: Path):
    """
    Files newly created by the callback should not be visited within the same
    `apply()` call.

    The test creates three files and an `apply()` callback that creates a new
    `*_clone.txt` for every visited path. After `apply()` returns, the
    recorded set is exactly the three original files; the clones exist on
    disk but were not visited.
    """
    originals = [tmp_path / f'f{i}.txt' for i in range(3)]
    for path in originals:
        path.touch()

    processed: List[Path] = []

    def cloning_callback(path: Path) -> None:
        processed.append(path)
        (tmp_path / f'{path.stem}_clone.txt').touch()

    Crawler(tmp_path, freeze=True).apply(cloning_callback)

    assert set(processed) == set(originals)
    for path in originals:
        clone = tmp_path / f'{path.stem}_clone.txt'
        assert clone.exists()
        assert clone not in processed


def test_freeze_apply_called_twice_rebuilds_snapshot_each_time(tmp_path: Path):
    """
    Two sequential `apply()` calls on the same frozen crawler should each
    rebuild the snapshot, so a mutation done by the first call is reflected
    by the second call.

    The test creates three files and uses one frozen crawler instance. The
    first `apply()` deletes every visited file; the second `apply()` collects
    visited paths into a list. Because nothing remains on disk between the
    calls, the second call must visit nothing — which is only possible if the
    snapshot is rebuilt freshly on every `apply()` invocation.
    """
    files = [tmp_path / f'f{i}.txt' for i in range(3)]
    for path in files:
        path.touch()

    crawler = Crawler(tmp_path, freeze=True)

    def delete_callback(path: Path) -> None:
        path.unlink()

    crawler.apply(delete_callback)

    assert all(not path.exists() for path in files)

    seen: List[Path] = []

    crawler.apply(seen.append)

    assert seen == []


@pytest.mark.parametrize(
    'token_route',
    [
        'instance',
        'call',
    ],
)
def test_freeze_apply_with_cancelled_token_does_not_call_callback(crawl_directory_path: Union[str, Path], token_route: str):
    """
    `apply()` under `freeze=True` with an already-cancelled token should not
    invoke the callback — whether the token is set on the instance or passed
    via `apply(token=...)`.

    The test parametrises both token-passing routes through
    `AbstractCrawler.apply → self.go(token)` and verifies that the callback
    is never called in either case.
    """
    seen: List[Path] = []

    cancelled = SimpleToken(cancelled=True)

    if token_route == 'instance':
        Crawler(crawl_directory_path, freeze=True, token=cancelled).apply(seen.append)
    else:
        Crawler(crawl_directory_path, freeze=True).apply(seen.append, token=cancelled)

    assert seen == []


@pytest.mark.parametrize(
    'mix',
    [
        lambda p1, p2: Crawler(p1, freeze=True) + Crawler(p2, freeze=True),
        lambda p1, p2: Crawler(p1, freeze=True) + Crawler(p2),
    ],
    ids=['both_frozen', 'one_frozen'],
)
def test_group_with_freeze_in_children_matches_unfrozen_group(
    mix,
    crawl_directory_path: Union[str, Path],
    second_crawl_directory_path: Union[str, Path],
):
    """
    A `CrawlersGroup` whose children are frozen (in part or in full) should
    yield the same deduplicated union as a fully-unfrozen group on an
    unchanging filesystem.

    The test parametrises two configurations — "both children frozen" and
    "only the first frozen" — and verifies that both produce the same set of
    paths as the group of unfrozen children.
    """
    expected = set(Crawler(crawl_directory_path) + Crawler(second_crawl_directory_path))

    assert set(mix(crawl_directory_path, second_crawl_directory_path)) == expected


def test_group_with_frozen_child_apply_with_deletion(tmp_path: Path):
    """
    A `CrawlersGroup` whose children are frozen `Crawler` instances should
    let `apply()` delete every visited file safely.

    The test creates two subdirectories with files, builds a group of two
    frozen crawlers (one per subdirectory), and runs `apply()` with an
    unlink-and-record callback. Every original file is recorded as visited
    and removed from disk.
    """
    sub_a = tmp_path / 'a'
    sub_b = tmp_path / 'b'
    sub_a.mkdir()
    sub_b.mkdir()
    files_a = [sub_a / f'a{i}.txt' for i in range(3)]
    files_b = [sub_b / f'b{i}.txt' for i in range(3)]
    for path in (*files_a, *files_b):
        path.touch()

    processed: List[Path] = []

    def callback(path: Path) -> None:
        processed.append(path)
        path.unlink()

    group = Crawler(sub_a, freeze=True) + Crawler(sub_b, freeze=True)
    group.apply(callback)

    assert set(processed) == set(files_a) | set(files_b)
    for path in (*files_a, *files_b):
        assert not path.exists()


def test_crawlers_group_rejects_freeze_keyword(crawl_directory_path: Union[str, Path]):
    """
    `CrawlersGroup.__init__` should reject a `freeze` keyword argument.

    The test guards the API contract spelled out in the plan: `freeze` is a
    per-child `Crawler` option, never a group-level one. If a user tries to
    pass `freeze=True` to a `CrawlersGroup` by analogy with `Crawler`, the
    standard `TypeError` from Python's constructor should surface.
    """
    if sys.version_info < (3, 10):
        expected_message = "__init__() got an unexpected keyword argument 'freeze'"
    else:
        expected_message = "CrawlersGroup.__init__() got an unexpected keyword argument 'freeze'"

    with pytest.raises(TypeError, match=match(expected_message)):
        CrawlersGroup([Crawler(crawl_directory_path)], freeze=True)  # type: ignore[call-arg]
