"""
Display and logging utilities for Plex Recommender.
Handles colored output, progress indicators, and formatting.
"""

import sys
import re
import logging
from typing import Dict, List

# ANSI color codes
RED = '\033[91m'
GREEN = '\033[92m'
YELLOW = '\033[93m'
CYAN = '\033[96m'
RESET = '\033[0m'

# ANSI pattern for stripping color codes from log files
ANSI_PATTERN = re.compile(r'\x1b\[[0-9;]*m')


class ColoredFormatter(logging.Formatter):
    """Custom formatter that adds colors to log levels"""

    LEVEL_COLORS = {
        logging.DEBUG: CYAN,
        logging.INFO: GREEN,
        logging.WARNING: YELLOW,
        logging.ERROR: RED,
        logging.CRITICAL: RED,
    }

    def format(self, record):
        # Add color to the level name
        color = self.LEVEL_COLORS.get(record.levelno, '')
        record.levelname = f"{color}{record.levelname}{RESET}"
        return super().format(record)


class TeeLogger:
    """
    A simple 'tee' class that writes to both console and a file,
    stripping ANSI color codes for the file and handling Unicode characters.
    """
    def __init__(self, logfile):
        self.logfile = logfile
        # Force UTF-8 encoding for stdout
        if hasattr(sys.stdout, 'buffer'):
            self.stdout_buffer = sys.stdout.buffer
        else:
            self.stdout_buffer = sys.stdout

    def write(self, text):
        try:
            # Write to console
            if hasattr(sys.stdout, 'buffer'):
                self.stdout_buffer.write(text.encode('utf-8'))
            else:
                sys.__stdout__.write(text)

            # Write to file (strip ANSI codes)
            stripped = ANSI_PATTERN.sub('', text)
            self.logfile.write(stripped)
        except UnicodeEncodeError:
            # Fallback for problematic characters
            safe_text = text.encode('ascii', 'replace').decode('ascii')
            if hasattr(sys.stdout, 'buffer'):
                self.stdout_buffer.write(safe_text.encode('utf-8'))
            else:
                sys.__stdout__.write(safe_text)
            stripped = ANSI_PATTERN.sub('', safe_text)
            self.logfile.write(stripped)

    def flush(self):
        if hasattr(sys.stdout, 'buffer'):
            self.stdout_buffer.flush()
        else:
            sys.__stdout__.flush()
        self.logfile.flush()


def setup_logging(debug: bool = False, config: dict = None) -> logging.Logger:
    """
    Configure logging for recommendation scripts.

    Args:
        debug: If True, set level to DEBUG. Otherwise use config or default to INFO.
        config: Optional config dict that may contain logging.level setting.

    Returns:
        Configured logger instance.
    """
    # Determine log level
    if debug:
        level = logging.DEBUG
    elif config and config.get('logging', {}).get('level'):
        level_str = config['logging']['level'].upper()
        level = getattr(logging, level_str, logging.INFO)
    else:
        level = logging.INFO

    # Create handler with colored formatter
    handler = logging.StreamHandler()
    handler.setLevel(level)

    formatter = logging.Formatter(
        fmt='%(asctime)s [%(levelname)s] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    handler.setFormatter(formatter)

    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    # Remove existing handlers to avoid duplicates
    root_logger.handlers = []
    root_logger.addHandler(handler)

    # Suppress noisy third-party loggers
    logging.getLogger('urllib3').setLevel(logging.WARNING)
    logging.getLogger('requests').setLevel(logging.WARNING)

    logger = logging.getLogger('plex_recommender')
    logger.setLevel(level)

    return logger


def print_user_header(username: str):
    """Print header when starting to process a user"""
    print(f"\n{GREEN}Processing recommendations for user: {username}{RESET}")
    print("-" * 50)


def print_user_footer(username: str):
    """Print footer when done processing a user"""
    print(f"\n{GREEN}Completed processing for user: {username}{RESET}")
    print("-" * 50)


def print_status(message: str, level: str = "info"):
    """Print a status message with appropriate color and log to file"""
    logger = logging.getLogger('plex_recommender')
    if level == "success":
        print(f"{GREEN}✓ {message}{RESET}")
        logger.info(message)
    elif level == "warning":
        log_warning(f"{message}")
        logger.warning(message)
    elif level == "error":
        log_error(f"{message}")
        logger.error(message)
    else:
        print(message)
        logger.info(message)


def log_warning(message: str):
    """Log warning and print with yellow color"""
    logger = logging.getLogger('plex_recommender')
    logger.warning(message)
    print(f"{YELLOW}{message}{RESET}")


def log_error(message: str):
    """Log error and print with red color"""
    logger = logging.getLogger('plex_recommender')
    logger.error(message)
    print(f"{RED}{message}{RESET}")


def show_progress(prefix: str, current: int, total: int):
    """
    Display progress indicator on same line.

    Args:
        prefix: Text prefix for progress display
        current: Current item number
        total: Total number of items
    """
    pct = int((current / total) * 100) if total > 0 else 0
    msg = f"\r{CYAN}{prefix} {current}/{total} ({pct}%){RESET}"
    sys.stdout.write(msg)
    sys.stdout.flush()
    if current == total:
        sys.stdout.write("\n")


def format_media_output(
    media_info: Dict,
    index: int = None,
    show_summary: bool = True,
    show_cast: bool = True,
    show_language: bool = True,
    show_rating: bool = True,
    show_imdb_link: bool = False,
    media_type: str = 'movie'
) -> str:
    """
    Format media item (movie or TV show) for display output.

    Args:
        media_info: Dict with title, year, genres, summary, cast, language, rating, etc.
        index: Optional 1-based index for numbered lists
        show_summary: Whether to include summary/overview
        show_cast: Whether to include cast list
        show_language: Whether to include original language
        show_rating: Whether to include TMDB rating
        show_imdb_link: Whether to include IMDB link
        media_type: 'movie' or 'tv' for proper formatting

    Returns:
        Formatted string for display
    """
    lines = []

    # Title line with optional index
    title = media_info.get('title', 'Unknown')
    year = media_info.get('year', '')
    similarity = media_info.get('similarity', media_info.get('score', 0))

    if index:
        title_line = f"{index}. {CYAN}{title}{RESET}"
    else:
        title_line = f"{CYAN}{title}{RESET}"

    if year:
        title_line += f" ({year})"

    if similarity:
        sim_pct = similarity if isinstance(similarity, str) else f"{similarity:.1%}"
        title_line += f" - Similarity: {YELLOW}{sim_pct}{RESET}"

    lines.append(title_line)

    # Genres
    genres = media_info.get('genres', [])
    if genres:
        genre_str = ', '.join(genres) if isinstance(genres, list) else genres
        lines.append(f"  {YELLOW}Genres:{RESET} {genre_str}")

    # Rating
    if show_rating:
        rating = media_info.get('rating', media_info.get('vote_average', 0))
        if rating:
            lines.append(f"  {YELLOW}Rating:{RESET} {rating:.1f}/10")

    # Language
    if show_language:
        language = media_info.get('language', media_info.get('original_language', ''))
        if language and language != 'N/A':
            lines.append(f"  {YELLOW}Language:{RESET} {language}")

    # Cast
    if show_cast:
        cast = media_info.get('cast', [])
        if cast:
            cast_str = ', '.join(cast[:5]) if isinstance(cast, list) else cast
            lines.append(f"  {YELLOW}Cast:{RESET} {cast_str}")

    # Director (movies) or Studio (TV)
    if media_type == 'movie':
        directors = media_info.get('directors', media_info.get('director', []))
        if directors:
            dir_str = ', '.join(directors) if isinstance(directors, list) else directors
            lines.append(f"  {YELLOW}Director:{RESET} {dir_str}")
    else:
        studio = media_info.get('studio', media_info.get('studios', ''))
        if studio:
            studio_str = studio if isinstance(studio, str) else ', '.join(studio[:2])
            lines.append(f"  {YELLOW}Studio:{RESET} {studio_str}")

    # Summary
    if show_summary:
        summary = media_info.get('summary', media_info.get('overview', ''))
        if summary:
            # Truncate long summaries
            if len(summary) > 200:
                summary = summary[:197] + "..."
            lines.append(f"  {summary}")

    # IMDB link
    if show_imdb_link:
        imdb_id = media_info.get('imdb_id')
        if imdb_id:
            lines.append(f"  {CYAN}https://www.imdb.com/title/{imdb_id}/{RESET}")

    return '\n'.join(lines)


def print_similarity_breakdown(media_info: Dict, score: float, breakdown: Dict, media_type: str = 'movie') -> None:
    """
    Print detailed similarity score breakdown for debugging.

    Args:
        media_info: Dict with title and other media info
        score: Total similarity score
        breakdown: Dict with component scores and details
        media_type: 'movie' or 'tv'
    """
    title = media_info.get('title', 'Unknown')
    print(f"\n{CYAN}=== Similarity Breakdown: {title} ==={RESET}")
    print(f"Total Score: {YELLOW}{score:.1%}{RESET}")
    print()

    # Component scores
    print(f"  Genre Score:    {breakdown.get('genre_score', 0):.3f}")
    if media_type == 'movie':
        print(f"  Director Score: {breakdown.get('director_score', 0):.3f}")
    else:
        print(f"  Studio Score:   {breakdown.get('studio_score', 0):.3f}")
    print(f"  Actor Score:    {breakdown.get('actor_score', 0):.3f}")
    print(f"  Keyword Score:  {breakdown.get('keyword_score', 0):.3f}")
    print(f"  Language Score: {breakdown.get('language_score', 0):.3f}")
    print()

    # Details
    details = breakdown.get('details', {})
    if details.get('genres'):
        print(f"  Matched Genres: {', '.join(details['genres'][:5])}")
    if details.get('actors'):
        print(f"  Matched Actors: {', '.join(details['actors'][:3])}")
    if details.get('keywords'):
        print(f"  Matched Keywords: {', '.join(details['keywords'][:5])}")


def user_select_recommendations(recommendations: List[Dict], operation_label: str) -> List[Dict]:
    """
    Present recommendations to user and let them select which to process.

    Args:
        recommendations: List of recommendation dicts with title, year, similarity, etc.
        operation_label: What operation will be done (e.g., "add to Radarr", "label")

    Returns:
        List of selected recommendations (empty if user skips)
    """
    if not recommendations:
        return []

    print(f"\n{CYAN}Found {len(recommendations)} recommendations:{RESET}")
    for i, rec in enumerate(recommendations, 1):
        title = rec.get('title', 'Unknown')
        year = rec.get('year', '')
        similarity = rec.get('similarity', rec.get('score', 0))
        sim_str = f"{similarity:.1%}" if isinstance(similarity, float) else similarity
        print(f"  {i}. {title} ({year}) - {sim_str}")

    print(f"\n{YELLOW}Options:{RESET}")
    print(f"  - Enter numbers to select (e.g., '1,3,5' or '1-5')")
    print(f"  - Enter 'all' to {operation_label} all")
    print(f"  - Enter 'none' or press Enter to skip")

    try:
        choice = input(f"\n{CYAN}Select items to {operation_label}: {RESET}").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return []

    if not choice or choice == 'none':
        return []

    if choice == 'all':
        return recommendations

    # Parse selection
    selected_indices = set()
    for part in choice.replace(' ', '').split(','):
        if '-' in part:
            try:
                start, end = part.split('-')
                selected_indices.update(range(int(start), int(end) + 1))
            except ValueError:
                continue
        else:
            try:
                selected_indices.add(int(part))
            except ValueError:
                continue

    # Return selected items (1-indexed to 0-indexed)
    selected = []
    for idx in sorted(selected_indices):
        if 1 <= idx <= len(recommendations):
            selected.append(recommendations[idx - 1])

    return selected
