import os
from pathlib import Path

import pytest


@pytest.fixture(params=[str, Path])
def walk_directory_path(request):
    return request.param(os.path.join('tests', 'test_files', 'walk_it'))
