"""Shared utility functions."""


def row_to_dict(row: tuple, columns: list[str]) -> dict:
    return dict(zip(columns, row))
