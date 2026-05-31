from pathlib import Path
from typing import Callable, List, Optional, Type, Union

from cantok import AbstractToken, DefaultToken

from dirstree.crawlers.crawler import Crawler


class PythonCrawler(Crawler):
    def __init__(
        self,
        *paths: Union[str, Path],
        exclude: Optional[List[str]] = None,
        filter: Optional[Callable[[Path], bool]] = None,  # noqa: A002
        token: AbstractToken = DefaultToken(),  # noqa: B008
        freeze: bool = False,
        raise_on_cancel: Union[bool, BaseException, Type[BaseException]] = False,
    ) -> None:
        super().__init__(
            *paths, extensions=('.py',), exclude=exclude, filter=filter, token=token, freeze=freeze, raise_on_cancel=raise_on_cancel,
        )
        self.addictional_repr_filters = {
            'extensions': lambda x: False,  # noqa: ARG005
        }
