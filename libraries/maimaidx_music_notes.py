from typing import Iterable, List, Union


def build_music_info_note_columns(notes: Iterable[int]) -> List[Union[int, str]]:
    """Return chart note counts in Total/Tap/Hold/Slide/Touch/Break order."""
    counts = list(notes)
    total = sum(counts)
    if len(counts) == 4:
        counts.insert(3, "-")
    return [total, *counts]
