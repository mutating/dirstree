import os
from pathlib import Path
from typing import Union

from dirstree import Crawler


def test_crawl_test_directory_with_default_python_extensions(crawl_directory_path: Union[str, Path]):
    crawler = Crawler(crawl_directory_path)

    expected_paths = [
        os.path.join('tests', 'test_files', 'walk_it', '__init__.py'),
        os.path.join('tests', 'test_files', 'walk_it', 'simple_code.py'),
        os.path.join('tests', 'test_files', 'walk_it', 'nested_folder', 'non_python_file.txt'),
        os.path.join('tests', 'test_files', 'walk_it', 'nested_folder', 'python_file.py'),
        os.path.join('tests', 'test_files', 'walk_it', 'nested_folder', '__init__.py'),
    ]
    real_paths = [str(x) for x in crawler.walk()]

    expected_paths.sort()
    real_paths.sort()

    assert real_paths == expected_paths


def test_crawl_test_directory_with_txt_extension(crawl_directory_path: Union[str, Path]):
    crawler = Crawler(crawl_directory_path, extensions=['.txt'])

    assert [str(x) for x in crawler.walk()] == [
        os.path.join('tests', 'test_files', 'walk_it', 'nested_folder', 'non_python_file.txt'),
    ]


def test_crawl_test_directory_with_py_extension(crawl_directory_path: Union[str, Path]):
    crawler = Crawler(crawl_directory_path, extensions=['.py'])

    expected_paths = [
        os.path.join('tests', 'test_files', 'walk_it', '__init__.py'),
        os.path.join('tests', 'test_files', 'walk_it', 'simple_code.py'),
        os.path.join('tests', 'test_files', 'walk_it', 'nested_folder', 'python_file.py'),
        os.path.join('tests', 'test_files', 'walk_it', 'nested_folder', '__init__.py'),
    ]
    real_paths = [str(x) for x in crawler.walk()]

    expected_paths.sort()
    real_paths.sort()

    assert real_paths == expected_paths


def test_crawl_test_directory_with_exclude_with_py_extension(crawl_directory_path: Union[str, Path]):
    crawler = Crawler(crawl_directory_path, exclude=['__init__.py'], extensions=['.py'])

    assert [str(x) for x in crawler.walk()] == [
        os.path.join('tests', 'test_files', 'walk_it', 'simple_code.py'),
        os.path.join('tests', 'test_files', 'walk_it', 'nested_folder', 'python_file.py'),
    ]


def test_crawl_test_directory_with_exclude_patterns_without_extensions(crawl_directory_path: Union[str, Path]):
    crawler = Crawler(crawl_directory_path, exclude=['__init__.py'])

    expected_paths = [
        os.path.join('tests', 'test_files', 'walk_it', 'simple_code.py'),
        os.path.join('tests', 'test_files', 'walk_it', 'nested_folder', 'non_python_file.txt'),
        os.path.join('tests', 'test_files', 'walk_it', 'nested_folder', 'python_file.py'),
    ]
    real_paths = [str(x) for x in crawler.walk()]

    expected_paths.sort()
    real_paths.sort()

    assert real_paths == expected_paths


def test_crawl_test_directory_with_exclude_patterns_and_extensions(crawl_directory_path: Union[str, Path]):
    crawler = Crawler(crawl_directory_path, extensions=['.txt'], exclude=['__init__.py'])

    assert [str(x) for x in crawler.walk()] == [
        os.path.join('tests', 'test_files', 'walk_it', 'nested_folder', 'non_python_file.txt'),
    ]
