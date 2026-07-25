"""
Display and logging utilities for Curatarr.
Handles colored output, progress indicators, and formatting.
"""

import sys
import re
import json
import logging
from datetime import datetime, timezone
from typing import Dict, List

from .redact import redact

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


# logging.LogRecord attributes that are always present on every record -
# used by JsonFormatter below to tell "standard" attributes apart from
# structured "extra" fields a call site attached via
# logger.info(msg, extra={...}). Anything not in this set (and not
# private, i.e. not leading with '_') is treated as a structured extra
# and included in the rendered JSON object verbatim.
_LOG_RECORD_RESERVED_ATTRS = frozenset({
    'name', 'msg', 'args', 'levelname', 'levelno', 'pathname', 'filename',
    'module', 'exc_info', 'exc_text', 'stack_info', 'lineno', 'funcName',
    'created', 'msecs', 'relativeCreated', 'thread', 'threadName',
    'processName', 'process', 'message', 'asctime', 'taskName',
})


class JsonFormatter(logging.Formatter):
    """Structured (JSON-lines) log formatter - opt-in via config.yml's
    `logging.format: json` (default stays ColoredFormatter above; see
    setup_logging). Every record renders as exactly one JSON object per
    line (JSON-lines, not a JSON array, so log aggregators/SIEMs can
    consume it as a stream) with a consistent field set:

        timestamp, level, logger, message

    plus whatever structured "extra" fields a call site attached, e.g.
    ``logger.info("run finished", extra={'user': u, 'engine': e,
    'duration': d})`` - those keys are included as their own top-level
    JSON fields, not folded into the message string.

    Redacted through the exact same utils.redact.redact() every other
    log destination in this codebase uses (see this module's own
    docstring and utils/redact.py's) - a secret-shaped value reaching a
    JSON log line in plaintext is exactly as unacceptable as it reaching
    the human-readable one. Applied to the rendered message, every
    string-valued extra field, and a formatted exception traceback
    (exc_info), since any of the three could in principle echo a token
    (e.g. a stray X-Plex-Token query parameter in an error message or in
    an extra field pulled from a response).
    """

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            'timestamp': datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            'level': record.levelname,
            'logger': record.name,
            'message': redact(record.getMessage()),
        }
        for key, value in record.__dict__.items():
            if key in _LOG_RECORD_RESERVED_ATTRS or key.startswith('_'):
                continue
            payload[key] = redact(value) if isinstance(value, str) else value
        if record.exc_info:
            payload['exception'] = redact(self.formatException(record.exc_info))
        return json.dumps(payload, default=str)


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
        # Redact before emitting to either destination (see
        # utils/redact.py) - a recommender/client error message could in
        # principle echo a token (e.g. a stray X-Plex-Token query
        # parameter), and neither the console nor the on-disk log file
        # this tees to should ever hold that in plaintext.
        text = redact(text)
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
        config: Optional config dict that may contain logging.level setting
            and an optional logging.format setting ('text', the default,
            human-readable/colored - or 'json', structured JSON-lines via
            JsonFormatter above - see that class's docstring). Unset/any
            other value falls back to 'text', so existing configs (which
            predate logging.format entirely) are completely unaffected.

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

    log_format = str((config or {}).get('logging', {}).get('format', 'text')).lower()

    # Create handler with the configured formatter - JSON opt-in via
    # logging.format: json, text (ColoredFormatter) otherwise/by default.
    handler = logging.StreamHandler()
    handler.setLevel(level)

    if log_format == 'json':
        formatter = JsonFormatter()
    else:
        formatter = ColoredFormatter(
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

    logger = logging.getLogger('curatarr')
    logger.setLevel(level)

    return logger


def print_user_header(username: str) -> None:
    """Print header when starting to process a user."""
    print(f"\n{GREEN}Processing recommendations for user: {username}{RESET}")
    print("-" * 50)


def print_user_footer(username: str) -> None:
    """Print footer when done processing a user."""
    print(f"\n{GREEN}Completed processing for user: {username}{RESET}")
    print("-" * 50)


def print_status(message: str, level: str = "info") -> None:
    """Print a status message with appropriate color and log to file."""
    logger = logging.getLogger('curatarr')
    if level == "success":
        print(f"{GREEN}✓ {message}{RESET}")
        logger.info(message)
    elif level == "warning":
        log_warning(message)
    elif level == "error":
        log_error(message)
    else:
        print(message)
        logger.info(message)


def log_info(message: str) -> None:
    """Log info message."""
    logger = logging.getLogger('curatarr')
    logger.info(message)


def log_warning(message: str) -> None:
    """Log warning with yellow color (via ColoredFormatter).

    Redacted before emitting (see utils/redact.py) - warnings frequently
    wrap raw exception text (a failed request's URL, an API error body),
    which could in principle contain a token.
    """
    logger = logging.getLogger('curatarr')
    logger.warning(redact(message))


def log_error(message: str) -> None:
    """Log error with red color (via ColoredFormatter).

    Redacted before emitting - see log_warning's docstring above.
    """
    logger = logging.getLogger('curatarr')
    logger.error(redact(message))


def clickable_link(url: str, text: str = None) -> str:
    """
    Create a clickable hyperlink for modern terminals using OSC 8 escape codes.

    Works in: iTerm2, Windows Terminal, GNOME Terminal, Konsole, and others.
    Falls back to plain text in unsupported terminals.

    Args:
        url: The URL to link to
        text: Display text (defaults to URL if not provided)

    Returns:
        Formatted string with OSC 8 hyperlink escape codes
    """
    if text is None:
        text = url
    # OSC 8 format: \033]8;;URL\033\\TEXT\033]8;;\033\\
    return f"\033]8;;{url}\033\\{text}\033]8;;\033\\"


def show_progress(prefix: str, current: int, total: int) -> None:
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
    media: Dict,
    media_type: str = 'movie',
    show_summary: bool = False,
    index: int = None,
    show_cast: bool = False,
    show_director: bool = False,
    show_language: bool = False,
    show_rating: bool = False,
    show_genres: bool = True,
    show_imdb_link: bool = False
) -> str:
    """
    Format media item (movie or TV show) for display output.

    Args:
        media: Dict with title, year, genres, summary, cast, language, rating, etc.
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
    title = media.get('title', 'Unknown')
    year = media.get('year', '')
    similarity = media.get('similarity_score', media.get('similarity', media.get('score', 0)))

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
    if show_genres:
        genres = media.get('genres', [])
        if genres:
            genre_str = ', '.join(genres) if isinstance(genres, list) else genres
            lines.append(f"  {YELLOW}Genres:{RESET} {genre_str}")

    # Rating
    if show_rating:
        rating = media.get('rating', media.get('vote_average', 0))
        if rating:
            lines.append(f"  {YELLOW}Rating:{RESET} {rating:.1f}/10")

    # Language
    if show_language:
        language = media.get('language', media.get('original_language', ''))
        if language and language != 'N/A':
            lines.append(f"  {YELLOW}Language:{RESET} {language}")

    # Cast
    if show_cast:
        cast = media.get('cast', [])
        if cast:
            cast_str = ', '.join(cast[:5]) if isinstance(cast, list) else cast
            lines.append(f"  {YELLOW}Cast:{RESET} {cast_str}")

    # Director (movies only when show_director is True)
    if show_director and media_type == 'movie':
        directors = media.get('directors', media.get('director', []))
        if directors:
            dir_str = ', '.join(directors) if isinstance(directors, list) else directors
            lines.append(f"  {YELLOW}Director:{RESET} {dir_str}")

    # Studio (TV shows)
    if media_type == 'tv':
        studio = media.get('studio', media.get('studios', ''))
        if studio:
            studio_str = studio if isinstance(studio, str) else ', '.join(studio[:2])
            lines.append(f"  {YELLOW}Studio:{RESET} {studio_str}")

    # Summary
    if show_summary:
        summary = media.get('summary', media.get('overview', ''))
        if summary:
            # Truncate long summaries
            if len(summary) > 200:
                summary = summary[:197] + "..."
            lines.append(f"  {summary}")

    # IMDB link
    if show_imdb_link:
        imdb_id = media.get('imdb_id')
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


def smart_open_html(file_path: str) -> bool:
    """
    Smart browser opening that reuses existing tabs when possible.

    Behavior:
    - If the page is already open: bring browser to focus and refresh
    - If not open: open in default browser (new tab in existing window, or new window)

    Args:
        file_path: Absolute path to the HTML file

    Returns:
        True if successful, False otherwise
    """
    import platform
    import subprocess
    import webbrowser

    file_url = f"file://{file_path}"
    system = platform.system()

    try:
        if system == "Darwin":
            return _open_html_macos(file_path, file_url)
        elif system == "Windows":
            return _open_html_windows(file_url)
        else:
            # Linux and others - fall back to webbrowser
            return _open_html_linux(file_url)
    except Exception as e:
        log_warning(f"Smart browser open failed: {e}, falling back to default")
        webbrowser.open(file_url)
        return True


def _open_html_macos(file_path: str, file_url: str) -> bool:
    """Handle browser opening on macOS using AppleScript."""
    import subprocess
    import webbrowser

    # Try Chrome first, then Safari
    for browser, script in [
        ("Google Chrome", _get_chrome_applescript(file_url)),
        ("Safari", _get_safari_applescript(file_url)),
    ]:
        try:
            # Check if browser is running
            check_running = subprocess.run(
                ["osascript", "-e", f'tell application "System Events" to (name of processes) contains "{browser}"'],
                capture_output=True, text=True, timeout=5
            )
            if "true" in check_running.stdout.lower():
                # Browser is running, use AppleScript to find/refresh or open tab
                result = subprocess.run(
                    ["osascript", "-e", script],
                    capture_output=True, text=True, timeout=10
                )
                if result.returncode == 0:
                    print(f"Opened in {browser}")
                    return True
        except (subprocess.TimeoutExpired, subprocess.SubprocessError):
            continue

    # No browser running or AppleScript failed - use system default
    subprocess.run(["open", file_url], check=True, timeout=10)
    print("Opened in default browser")
    return True


def _get_chrome_applescript(file_url: str) -> str:
    """Generate AppleScript for Chrome: find existing tab or open new one."""
    return f'''
    tell application "Google Chrome"
        set found to false
        set targetURL to "{file_url}"

        repeat with w in windows
            set tabIndex to 0
            repeat with t in tabs of w
                set tabIndex to tabIndex + 1
                if URL of t starts with "file://" and URL of t contains "watchlist.html" then
                    set found to true
                    set active tab index of w to tabIndex
                    set index of w to 1
                    tell t to reload
                    activate
                    exit repeat
                end if
            end repeat
            if found then exit repeat
        end repeat

        if not found then
            if (count of windows) > 0 then
                tell front window to make new tab with properties {{URL:targetURL}}
            else
                make new window
                set URL of active tab of front window to targetURL
            end if
            activate
        end if
    end tell
    '''


def _get_safari_applescript(file_url: str) -> str:
    """Generate AppleScript for Safari: find existing tab or open new one."""
    return f'''
    tell application "Safari"
        set found to false
        set targetURL to "{file_url}"

        repeat with w in windows
            set tabIndex to 0
            repeat with t in tabs of w
                set tabIndex to tabIndex + 1
                if URL of t starts with "file://" and URL of t contains "watchlist.html" then
                    set found to true
                    set current tab of w to t
                    set index of w to 1
                    tell t to do JavaScript "location.reload()"
                    activate
                    exit repeat
                end if
            end repeat
            if found then exit repeat
        end repeat

        if not found then
            if (count of windows) > 0 then
                tell front window to make new tab with properties {{URL:targetURL}}
            else
                make new document with properties {{URL:targetURL}}
            end if
            activate
        end if
    end tell
    '''


def _open_html_windows(file_url: str) -> bool:
    """Handle browser opening on Windows."""
    import os
    import webbrowser

    # Try to use PowerShell to check for existing browser windows
    # For now, just use the default browser - Windows doesn't have easy tab reuse
    #
    # os.startfile() (not subprocess.run(["start", "", file_url],
    # shell=True, ...)) - shell=True runs the whole argv through
    # cmd.exe, which is unnecessary indirection for what's just "ask
    # Windows to open this with its default handler", and drops the
    # needless shell=True attack surface (cmd.exe metacharacter
    # interpretation of file_url) entirely. os.startfile() is the direct
    # ShellExecute equivalent of double-clicking the file/URL - no shell
    # involved at all.
    try:
        os.startfile(file_url)
        print("Opened in default browser")
        return True
    except OSError:
        webbrowser.open(file_url)
        return True


def _open_html_linux(file_url: str) -> bool:
    """Handle browser opening on Linux."""
    import subprocess
    import webbrowser

    try:
        subprocess.run(["xdg-open", file_url], check=True, timeout=10)
        print("Opened in default browser")
        return True
    except (subprocess.SubprocessError, FileNotFoundError):
        webbrowser.open(file_url)
        return True
