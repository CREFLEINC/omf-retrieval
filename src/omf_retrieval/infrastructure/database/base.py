"""Declarative base for OMF Retrieval database mappings."""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Own the metadata shared by all application model mappings."""
