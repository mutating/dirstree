# dirstree: an another library for iterating through the contents of a directory

[![Downloads](https://static.pepy.tech/badge/dirstree/month)](https://pepy.tech/project/dirstree)
[![Downloads](https://static.pepy.tech/badge/dirstree)](https://pepy.tech/project/dirstree)
[![Coverage Status](https://coveralls.io/repos/github/pomponchik/dirstree/badge.svg?branch=main)](https://coveralls.io/github/pomponchik/dirstree?branch=main)
[![Lines of code](https://sloc.xyz/github/pomponchik/dirstree/?category=code)](https://github.com/boyter/scc/)
[![Hits-of-Code](https://hitsofcode.com/github/pomponchik/dirstree?branch=main)](https://hitsofcode.com/github/pomponchik/dirstree/view?branch=main)
[![Test-Package](https://github.com/pomponchik/dirstree/actions/workflows/tests_and_coverage.yml/badge.svg)](https://github.com/pomponchik/dirstree/actions/workflows/tests_and_coverage.yml)
[![Python versions](https://img.shields.io/pypi/pyversions/dirstree.svg)](https://pypi.python.org/pypi/dirstree)
[![PyPI version](https://badge.fury.io/py/dirstree.svg)](https://badge.fury.io/py/dirstree)
[![Checked with mypy](http://www.mypy-lang.org/static/mypy_badge.svg)](http://mypy-lang.org/)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)

There are many libraries for traversing directories. You can also do this using the standard library. This particular library is very different in that:

- Supports filtering by file extensions.
- Supports filtering in the [`.gitignore` format](https://git-scm.com/book/en/v2/Git-Basics-Recording-Changes-to-the-Repository#_ignoring).
- Natively works with both [`Path` objects](https://docs.python.org/3/library/pathlib.html#basic-use) from the standard library and strings.


## Table of contents

- [**Installation**](#installation)
- [**Basic usage**](#basic-usage)
- [**Filtering**](#filtering)


## Installation

You can install [`dirstree`](https://pypi.python.org/pypi/dirstree) using pip:

```bash
pip install dirstree
```

You can also quickly try out this and other packages without having to install using [instld](https://github.com/pomponchik/instld).


## Basic usage

It's very easy to work with the library in your own code:

- Create a crawler object, passing the path to the base directory and, if necessary, additional arguments.
- Iterate recursively through the files in this directory using the `.walk()` method.

The simplest code example would look like this:

```python
from dirstree import DirectoryWalker

walker = DirectoryWalker('.')

for file in walker.walk():
    print(file)
```

> ↑ Here we output recursively (that is, including the contents of nested directories) all files from the current directory. At each iteration, we get a new [`Path` object](https://docs.python.org/3/library/pathlib.html#basic-use).


## Filtering

Iterating through the files in the directory, you may not want to view all files, but only files of a certain type. To do this, ignore all other files. How to do it? There are 2 ways:

- Bypass only files with the specified [extensions](https://en.wikipedia.org/wiki/Filename_extension), such as `.txt`, `.doc`, or `.py`.
- Bypass files whose paths follow a specific text pattern.

To select a specific method, you need to pass a specific parameter when creating the crawler object. Of course, all the methods can be combined with each other.

To set the file extensions you are interested in, use the `extensions` parameter:

```python
walker = DirectoryWalker('.', extensions=['.txt'])  # Iterate only on .txt files.
```

To specify which files and directories you do NOT want to iterate over, use the `exclude` parameter:

```python
walker = DirectoryWalker('.', exclude=['.git', 'venv'])  # Exclude ".git" and "venv" directories.
```

> ↑ Please note that we use the [`.gitignore` format](https://git-scm.com/book/en/v2/Git-Basics-Recording-Changes-to-the-Repository#_ignoring) here.
