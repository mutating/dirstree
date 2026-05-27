from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Callable, Generator

from cantok import AbstractToken, DefaultToken
from sigmatch import PossibleCallMatcher


class AbstractCrawler(ABC):
    def __iter__(self) -> Generator[Path, None, None]:
        yield from self.go()

    def __add__(self, other: 'AbstractCrawler') -> 'AbstractCrawler':
        if not isinstance(other, AbstractCrawler):
            raise TypeError(f"Cannot add {type(self).__name__} and {type(other).__name__}.")

        from dirstree.crawlers.group import CrawlersGroup  # noqa: PLC0415

        return CrawlersGroup([self, other])

    def apply(
        self,
        function: Callable[[Path], Any],
        token: AbstractToken = DefaultToken(),  # noqa: B008
    ) -> None:
        PossibleCallMatcher('.').match(function, raise_exception=True)
        for path in self.go(token):
            function(path)

    @abstractmethod
    def go(self, token: AbstractToken = DefaultToken()) -> Generator[Path, None, None]:  # noqa: B008
        ...  # pragma: no cover
