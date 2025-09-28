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


## Installation

You can install `dirstree` using pip:

```bash
pip install dirstree
```

You can also quickly try out this and other packages without having to install using [instld](https://github.com/pomponchik/instld).


## Basic usage
