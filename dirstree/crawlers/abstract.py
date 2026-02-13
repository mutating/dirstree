from abc import ABC, abstractmethod
from pathlib import Path
from typing import Generator

from cantok import AbstractToken, DefaultToken


class AbstractCrawler(ABC):
    def __iter__(self) -> Generator[Path, None, None]:
        yield from self.go()

    def __add__(self, other: 'AbstractCrawler') -> 'AbstractCrawler':
        if not isinstance(other, AbstractCrawler):
            raise TypeError(f"Cannot add {type(self).__name__} and {type(other).__name__}.")

        from dirstree.crawlers.group import CrawlersGroup  # noqa: PLC0415

        return CrawlersGroup([self, other])

    @abstractmethod
    def go(self, token: AbstractToken = DefaultToken()) -> Generator[Path, None, None]:  # noqa: B008
        ...  # pragma: no cover
