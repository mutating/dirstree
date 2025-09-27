import os
from pathlib import Path
from typing import Union

from dirstree import DirectoryWalker



def test_walk_test_directory_with_default_python_extensions(walk_directory_path: Union[str, Path]):
    walker = DirectoryWalker(walk_directory_path)

    print([str(x) for x in DirectoryWalker(walk_directory_path).walk()])
    assert [str(x) for x in walker.walk()] == [
        os.path.join('tests', 'test_files', 'walk_it', '__init__.py'),
        os.path.join('tests', 'test_files', 'walk_it', 'simple_code.py'),
        os.path.join('tests', 'test_files', 'walk_it', 'nested_folder', 'python_file.py'),
        os.path.join('tests', 'test_files', 'walk_it', 'nested_folder', '__init__.py'),
    ]


def test_walk_test_directory_with_txt_extension(walk_directory_path: Union[str, Path]):
    walker = DirectoryWalker(walk_directory_path, extensions=['.txt'])

    assert [str(x) for x in walker.walk()] == [
        os.path.join('tests', 'test_files', 'walk_it', 'nested_folder', 'non_python_file.txt'),
    ]


def test_walk_test_directory_with_exclude_patterns(walk_directory_path: Union[str, Path]):
    walker = DirectoryWalker(walk_directory_path, exclude_patterns=['__init__.py'])

    assert [str(x) for x in walker.walk()] == [
        os.path.join('tests', 'test_files', 'walk_it', 'simple_code.py'),
        os.path.join('tests', 'test_files', 'walk_it', 'nested_folder', 'python_file.py'),
    ]


def test_walk_test_directory_with_exclude_patterns_and_extensions(walk_directory_path: Union[str, Path]):
    walker = DirectoryWalker(walk_directory_path, extensions=['.txt'], exclude_patterns=['__init__.py'])

    assert [str(x) for x in walker.walk()] == [
        os.path.join('tests', 'test_files', 'walk_it', 'nested_folder', 'non_python_file.txt'),
    ]
