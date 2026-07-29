from typing import Callable, List


MUSIC_INFO_RATING_ACHIEVEMENTS = (100.5, 100.0, 99.5, 99.0, 98.0, 97.0)
MUSIC_INFO_RATING_X_POSITIONS = (295, 420, 545, 670, 795, 920, 1045)


def build_music_info_ratings(
    ds: float,
    rating_calculator: Callable[[float, float], int],
) -> List[int]:
    """Return AP, SSS+, SSS, SS+, SS, S+ and S ratings in display order."""
    standard_ratings = [
        rating_calculator(ds, achievement)
        for achievement in MUSIC_INFO_RATING_ACHIEVEMENTS
    ]
    ap_rating = standard_ratings[0] + 1
    return [ap_rating, *standard_ratings]
