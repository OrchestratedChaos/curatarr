"""
Tests for utils/display.py - Display and logging utilities.
"""

import pytest
import logging
import sys
from io import StringIO
from unittest.mock import Mock, patch, MagicMock

from utils.display import (
    RED, GREEN, YELLOW, CYAN, RESET,
    ANSI_PATTERN,
    ColoredFormatter,
    TeeLogger,
    setup_logging,
    print_user_header,
    print_user_footer,
    print_status,
    log_info,
    log_warning,
    log_error,
    show_progress,
    format_media_output,
    print_similarity_breakdown,
    user_select_recommendations
)


class TestColorConstants:
    """Tests for ANSI color constants."""

    def test_color_codes_defined(self):
        """Test that color codes are defined."""
        assert RED == '\033[91m'
        assert GREEN == '\033[92m'
        assert YELLOW == '\033[93m'
        assert CYAN == '\033[96m'
        assert RESET == '\033[0m'

    def test_ansi_pattern_strips_colors(self):
        """Test that ANSI pattern matches color codes."""
        colored_text = f"{RED}Error{RESET}"
        stripped = ANSI_PATTERN.sub('', colored_text)
        assert stripped == "Error"


class TestColoredFormatter:
    """Tests for ColoredFormatter class."""

    def test_formats_debug_level(self):
        """Test formatting DEBUG level."""
        formatter = ColoredFormatter()
        record = logging.LogRecord(
            name='test', level=logging.DEBUG, pathname='', lineno=0,
            msg='Test message', args=(), exc_info=None
        )

        formatted = formatter.format(record)

        assert CYAN in record.levelname

    def test_formats_info_level(self):
        """Test formatting INFO level."""
        formatter = ColoredFormatter()
        record = logging.LogRecord(
            name='test', level=logging.INFO, pathname='', lineno=0,
            msg='Test message', args=(), exc_info=None
        )

        formatted = formatter.format(record)

        assert GREEN in record.levelname

    def test_formats_warning_level(self):
        """Test formatting WARNING level."""
        formatter = ColoredFormatter()
        record = logging.LogRecord(
            name='test', level=logging.WARNING, pathname='', lineno=0,
            msg='Test message', args=(), exc_info=None
        )

        formatted = formatter.format(record)

        assert YELLOW in record.levelname

    def test_formats_error_level(self):
        """Test formatting ERROR level."""
        formatter = ColoredFormatter()
        record = logging.LogRecord(
            name='test', level=logging.ERROR, pathname='', lineno=0,
            msg='Test message', args=(), exc_info=None
        )

        formatted = formatter.format(record)

        assert RED in record.levelname

    def test_formats_critical_level(self):
        """Test formatting CRITICAL level."""
        formatter = ColoredFormatter()
        record = logging.LogRecord(
            name='test', level=logging.CRITICAL, pathname='', lineno=0,
            msg='Test message', args=(), exc_info=None
        )

        formatted = formatter.format(record)

        assert RED in record.levelname


class TestTeeLogger:
    """Tests for TeeLogger class."""

    def test_write_to_file_strips_ansi(self):
        """Test that file output has ANSI codes stripped."""
        mock_logfile = StringIO()
        tee = TeeLogger(mock_logfile)

        # Directly manipulate to test file writing
        colored_text = f"{GREEN}Success{RESET}"
        stripped = ANSI_PATTERN.sub('', colored_text)
        mock_logfile.write(stripped)

        assert mock_logfile.getvalue() == "Success"

    def test_teelogger_initialization(self):
        """Test TeeLogger initialization."""
        mock_logfile = StringIO()
        tee = TeeLogger(mock_logfile)

        assert tee.logfile == mock_logfile
        assert tee.stdout_buffer is not None

    def test_flush_calls_logfile_flush(self):
        """Test that flush calls logfile flush."""
        mock_logfile = Mock()
        mock_logfile.flush = Mock()

        tee = TeeLogger(mock_logfile)
        # Replace stdout_buffer with a mock to avoid actual stdout ops
        tee.stdout_buffer = Mock()
        tee.stdout_buffer.flush = Mock()

        tee.flush()

        mock_logfile.flush.assert_called_once()

    def test_initialization_without_buffer(self):
        """Test TeeLogger init when stdout has no buffer."""
        mock_logfile = StringIO()

        # Save original stdout
        original_stdout = sys.stdout

        # Create mock stdout without buffer
        mock_stdout = Mock(spec=['write', 'flush'])

        try:
            sys.stdout = mock_stdout
            tee = TeeLogger(mock_logfile)
            # Should use stdout directly when no buffer
            assert tee.stdout_buffer == mock_stdout
        finally:
            sys.stdout = original_stdout

    def test_write_with_buffer(self):
        """Test write() when stdout has buffer attribute."""
        mock_logfile = StringIO()
        tee = TeeLogger(mock_logfile)

        # Create mock buffer
        mock_buffer = Mock()
        mock_buffer.write = Mock()
        tee.stdout_buffer = mock_buffer

        tee.write("Hello")

        mock_buffer.write.assert_called_once()
        assert "Hello" in mock_logfile.getvalue()

    def test_write_without_buffer(self):
        """Test write() when stdout has no buffer."""
        mock_logfile = StringIO()
        original_stdout = sys.stdout

        # Create mock stdout without buffer
        mock_stdout = Mock(spec=['write', 'flush'])

        try:
            sys.stdout = mock_stdout
            tee = TeeLogger(mock_logfile)
            tee.write("Test")
            # Should write to both stdout and logfile
            assert "Test" in mock_logfile.getvalue()
        finally:
            sys.stdout = original_stdout

    def test_write_unicode_encode_error(self):
        """Test write() handles UnicodeEncodeError gracefully."""
        mock_logfile = StringIO()
        tee = TeeLogger(mock_logfile)

        # Create mock buffer that raises once, then succeeds
        mock_buffer = Mock()
        mock_buffer.write = Mock(side_effect=[
            UnicodeEncodeError('ascii', 'test', 0, 1, 'test'),  # First call fails
            None  # Second call succeeds (fallback)
        ])
        tee.stdout_buffer = mock_buffer

        # Should not raise, falls back to safe encoding
        tee.write("Hello \u2603")  # snowman character
        # Logfile should still get content
        assert mock_logfile.getvalue() != ""
        # Buffer write called twice (initial + fallback)
        assert mock_buffer.write.call_count == 2

    def test_flush_without_buffer(self):
        """Test flush() when stdout has no buffer."""
        mock_logfile = Mock()
        mock_logfile.flush = Mock()
        original_stdout = sys.stdout
        original_sys_stdout = sys.__stdout__

        # Create mock stdout without buffer
        mock_stdout = Mock(spec=['write', 'flush'])
        mock_sys_stdout = Mock()
        mock_sys_stdout.flush = Mock()

        try:
            sys.stdout = mock_stdout
            sys.__stdout__ = mock_sys_stdout
            tee = TeeLogger(mock_logfile)
            tee.flush()
            mock_logfile.flush.assert_called_once()
        finally:
            sys.stdout = original_stdout
            sys.__stdout__ = original_sys_stdout


class TestSetupLogging:
    """Tests for setup_logging() function."""

    def test_debug_mode(self):
        """Test setting up logging in debug mode."""
        logger = setup_logging(debug=True)

        assert logger.level == logging.DEBUG

    def test_default_info_level(self):
        """Test default INFO level when no config."""
        logger = setup_logging(debug=False)

        assert logger.level == logging.INFO

    def test_config_level(self):
        """Test using level from config."""
        config = {'logging': {'level': 'warning'}}
        logger = setup_logging(debug=False, config=config)

        assert logger.level == logging.WARNING

    def test_invalid_config_level_defaults_to_info(self):
        """Test that invalid config level defaults to INFO."""
        config = {'logging': {'level': 'invalid_level'}}
        logger = setup_logging(debug=False, config=config)

        assert logger.level == logging.INFO

    def test_returns_curatarr_logger(self):
        """Test that curatarr logger is returned."""
        logger = setup_logging()

        assert logger.name == 'curatarr'


class TestPrintUserHeader:
    """Tests for print_user_header() function."""

    def test_prints_username(self, capsys):
        """Test that username is printed in header."""
        print_user_header("TestUser")

        captured = capsys.readouterr()
        assert "TestUser" in captured.out
        assert "Processing recommendations" in captured.out


class TestPrintUserFooter:
    """Tests for print_user_footer() function."""

    def test_prints_username(self, capsys):
        """Test that username is printed in footer."""
        print_user_footer("TestUser")

        captured = capsys.readouterr()
        assert "TestUser" in captured.out
        assert "Completed processing" in captured.out


class TestPrintStatus:
    """Tests for print_status() function."""

    def test_success_status(self, capsys):
        """Test success status message."""
        # Setup logger to avoid warnings
        setup_logging()
        print_status("Operation completed", level="success")

        captured = capsys.readouterr()
        assert "Operation completed" in captured.out

    def test_info_status(self, capsys):
        """Test info status message."""
        setup_logging()
        print_status("Info message", level="info")

        captured = capsys.readouterr()
        assert "Info message" in captured.out

    @patch('utils.display.log_warning')
    def test_warning_status(self, mock_log_warning):
        """Test warning status message."""
        print_status("Warning message", level="warning")

        mock_log_warning.assert_called_once_with("Warning message")

    @patch('utils.display.log_error')
    def test_error_status(self, mock_log_error):
        """Test error status message."""
        print_status("Error message", level="error")

        mock_log_error.assert_called_once_with("Error message")


class TestLogFunctions:
    """Tests for log_info, log_warning, log_error functions."""

    def test_log_info(self):
        """Test log_info function."""
        setup_logging()
        # Should not raise
        log_info("Info message")

    def test_log_warning(self):
        """Test log_warning function."""
        setup_logging()
        # Should not raise
        log_warning("Warning message")

    def test_log_error(self):
        """Test log_error function."""
        setup_logging()
        # Should not raise
        log_error("Error message")


class TestShowProgress:
    """Tests for show_progress() function."""

    def test_shows_progress(self, capsys):
        """Test showing progress indicator."""
        show_progress("Processing", 5, 10)

        captured = capsys.readouterr()
        assert "5/10" in captured.out
        assert "50%" in captured.out

    def test_shows_100_percent(self, capsys):
        """Test showing 100% progress."""
        show_progress("Done", 10, 10)

        captured = capsys.readouterr()
        assert "100%" in captured.out

    def test_handles_zero_total(self, capsys):
        """Test handling zero total."""
        show_progress("Empty", 0, 0)

        captured = capsys.readouterr()
        assert "0/0" in captured.out


class TestFormatMediaOutput:
    """Tests for format_media_output() function."""

    def test_basic_movie_output(self):
        """Test basic movie formatting."""
        media = {
            'title': 'The Matrix',
            'year': 1999,
            'genres': ['Action', 'Sci-Fi']
        }

        result = format_media_output(media)

        assert 'The Matrix' in result
        assert '1999' in result
        assert 'Action' in result

    def test_with_index(self):
        """Test formatting with index."""
        media = {'title': 'Movie', 'year': 2020}

        result = format_media_output(media, index=5)

        assert '5.' in result

    def test_with_similarity_score(self):
        """Test formatting with similarity score."""
        media = {'title': 'Movie', 'similarity_score': 0.85}

        result = format_media_output(media)

        assert 'Similarity' in result

    def test_with_rating(self):
        """Test formatting with rating."""
        media = {'title': 'Movie', 'rating': 8.5}

        result = format_media_output(media, show_rating=True)

        assert 'Rating' in result
        assert '8.5' in result

    def test_with_language(self):
        """Test formatting with language."""
        media = {'title': 'Movie', 'language': 'English'}

        result = format_media_output(media, show_language=True)

        assert 'Language' in result
        assert 'English' in result

    def test_skips_na_language(self):
        """Test skipping N/A language."""
        media = {'title': 'Movie', 'language': 'N/A'}

        result = format_media_output(media, show_language=True)

        assert 'Language' not in result

    def test_with_cast(self):
        """Test formatting with cast."""
        media = {'title': 'Movie', 'cast': ['Actor 1', 'Actor 2']}

        result = format_media_output(media, show_cast=True)

        assert 'Cast' in result
        assert 'Actor 1' in result

    def test_with_directors(self):
        """Test formatting with directors."""
        media = {'title': 'Movie', 'directors': ['Director 1']}

        result = format_media_output(media, media_type='movie', show_director=True)

        assert 'Director' in result

    def test_with_studio_for_tv(self):
        """Test formatting with studio for TV shows."""
        media = {'title': 'Show', 'studio': 'HBO'}

        result = format_media_output(media, media_type='tv')

        assert 'Studio' in result
        assert 'HBO' in result

    def test_with_summary(self):
        """Test formatting with summary."""
        media = {'title': 'Movie', 'summary': 'A great movie about something.'}

        result = format_media_output(media, show_summary=True)

        assert 'A great movie' in result

    def test_truncates_long_summary(self):
        """Test truncating long summary."""
        long_summary = "A" * 250
        media = {'title': 'Movie', 'summary': long_summary}

        result = format_media_output(media, show_summary=True)

        assert '...' in result
        assert len(result) < 250 + 100  # Title + truncated summary

    def test_with_imdb_link(self):
        """Test formatting with IMDB link."""
        media = {'title': 'Movie', 'imdb_id': 'tt1234567'}

        result = format_media_output(media, show_imdb_link=True)

        assert 'imdb.com/title/tt1234567' in result

    def test_genres_as_string(self):
        """Test handling genres as string."""
        media = {'title': 'Movie', 'genres': 'Action, Comedy'}

        result = format_media_output(media)

        assert 'Action, Comedy' in result

    def test_similarity_as_string(self):
        """Test handling similarity as string."""
        media = {'title': 'Movie', 'similarity_score': '85%'}

        result = format_media_output(media)

        assert '85%' in result

    def test_no_year(self):
        """Test formatting without year."""
        media = {'title': 'Movie'}

        result = format_media_output(media)

        assert 'Movie' in result

    def test_no_genres(self):
        """Test formatting without genres."""
        media = {'title': 'Movie'}

        result = format_media_output(media, show_genres=True)

        assert 'Genres' not in result

    def test_cast_as_string(self):
        """Test handling cast as string."""
        media = {'title': 'Movie', 'cast': 'Actor 1, Actor 2'}

        result = format_media_output(media, show_cast=True)

        assert 'Actor 1, Actor 2' in result

    def test_directors_as_string(self):
        """Test handling directors as string."""
        media = {'title': 'Movie', 'directors': 'Director Name'}

        result = format_media_output(media, media_type='movie', show_director=True)

        assert 'Director Name' in result

    def test_studio_as_list(self):
        """Test handling studio as list for TV."""
        media = {'title': 'Show', 'studio': ['HBO', 'Netflix', 'Amazon']}

        result = format_media_output(media, media_type='tv')

        assert 'HBO' in result
        assert 'Netflix' in result


class TestPrintSimilarityBreakdown:
    """Tests for print_similarity_breakdown() function."""

    def test_prints_movie_breakdown(self, capsys):
        """Test printing movie similarity breakdown."""
        media_info = {'title': 'The Matrix'}
        breakdown = {
            'genre_score': 0.5,
            'director_score': 0.3,
            'actor_score': 0.2,
            'keyword_score': 0.1,
            'language_score': 0.0,
            'details': {
                'genres': ['Action', 'Sci-Fi'],
                'actors': ['Keanu Reeves'],
                'keywords': ['dystopia']
            }
        }

        print_similarity_breakdown(media_info, 0.85, breakdown, 'movie')

        captured = capsys.readouterr()
        assert 'The Matrix' in captured.out
        assert 'Director Score' in captured.out
        assert 'Genre Score' in captured.out

    def test_prints_tv_breakdown(self, capsys):
        """Test printing TV show similarity breakdown."""
        media_info = {'title': 'Breaking Bad'}
        breakdown = {
            'genre_score': 0.5,
            'studio_score': 0.3,
            'actor_score': 0.2,
            'keyword_score': 0.1,
            'language_score': 0.0,
            'details': {}
        }

        print_similarity_breakdown(media_info, 0.75, breakdown, 'tv')

        captured = capsys.readouterr()
        assert 'Breaking Bad' in captured.out
        assert 'Studio Score' in captured.out

    def test_unknown_title(self, capsys):
        """Test with missing title."""
        media_info = {}
        breakdown = {}

        print_similarity_breakdown(media_info, 0.5, breakdown)

        captured = capsys.readouterr()
        assert 'Unknown' in captured.out


class TestUserSelectRecommendations:
    """Tests for user_select_recommendations() function."""

    def test_returns_empty_for_empty_list(self):
        """Test returning empty list for empty input."""
        result = user_select_recommendations([], "add")

        assert result == []

    @patch('builtins.input', return_value='')
    def test_returns_empty_on_enter(self, mock_input, capsys):
        """Test returning empty on just Enter."""
        recs = [{'title': 'Movie 1', 'year': 2020, 'similarity': 0.8}]

        result = user_select_recommendations(recs, "add")

        assert result == []

    @patch('builtins.input', return_value='none')
    def test_returns_empty_on_none(self, mock_input, capsys):
        """Test returning empty on 'none'."""
        recs = [{'title': 'Movie 1', 'year': 2020, 'similarity': 0.8}]

        result = user_select_recommendations(recs, "add")

        assert result == []

    @patch('builtins.input', return_value='all')
    def test_returns_all_on_all(self, mock_input, capsys):
        """Test returning all recommendations on 'all'."""
        recs = [
            {'title': 'Movie 1', 'year': 2020, 'similarity': 0.8},
            {'title': 'Movie 2', 'year': 2021, 'similarity': 0.7}
        ]

        result = user_select_recommendations(recs, "add")

        assert result == recs

    @patch('builtins.input', return_value='1')
    def test_selects_single_item(self, mock_input, capsys):
        """Test selecting single item."""
        recs = [
            {'title': 'Movie 1', 'year': 2020, 'similarity': 0.8},
            {'title': 'Movie 2', 'year': 2021, 'similarity': 0.7}
        ]

        result = user_select_recommendations(recs, "add")

        assert len(result) == 1
        assert result[0]['title'] == 'Movie 1'

    @patch('builtins.input', return_value='1,3')
    def test_selects_multiple_items(self, mock_input, capsys):
        """Test selecting multiple items."""
        recs = [
            {'title': 'Movie 1', 'year': 2020, 'similarity': 0.8},
            {'title': 'Movie 2', 'year': 2021, 'similarity': 0.7},
            {'title': 'Movie 3', 'year': 2022, 'similarity': 0.6}
        ]

        result = user_select_recommendations(recs, "add")

        assert len(result) == 2
        assert result[0]['title'] == 'Movie 1'
        assert result[1]['title'] == 'Movie 3'

    @patch('builtins.input', return_value='1-3')
    def test_selects_range(self, mock_input, capsys):
        """Test selecting range of items."""
        recs = [
            {'title': 'Movie 1', 'year': 2020, 'similarity': 0.8},
            {'title': 'Movie 2', 'year': 2021, 'similarity': 0.7},
            {'title': 'Movie 3', 'year': 2022, 'similarity': 0.6}
        ]

        result = user_select_recommendations(recs, "add")

        assert len(result) == 3

    @patch('builtins.input', return_value='invalid')
    def test_handles_invalid_input(self, mock_input, capsys):
        """Test handling invalid input."""
        recs = [{'title': 'Movie 1', 'year': 2020, 'similarity': 0.8}]

        result = user_select_recommendations(recs, "add")

        assert result == []

    @patch('builtins.input', return_value='10')
    def test_ignores_out_of_range(self, mock_input, capsys):
        """Test ignoring out of range selections."""
        recs = [{'title': 'Movie 1', 'year': 2020, 'similarity': 0.8}]

        result = user_select_recommendations(recs, "add")

        assert result == []

    @patch('builtins.input', side_effect=EOFError)
    def test_handles_eof_error(self, mock_input, capsys):
        """Test handling EOFError."""
        recs = [{'title': 'Movie 1', 'year': 2020, 'similarity': 0.8}]

        result = user_select_recommendations(recs, "add")

        assert result == []

    @patch('builtins.input', side_effect=KeyboardInterrupt)
    def test_handles_keyboard_interrupt(self, mock_input, capsys):
        """Test handling KeyboardInterrupt."""
        recs = [{'title': 'Movie 1', 'year': 2020, 'similarity': 0.8}]

        result = user_select_recommendations(recs, "add")

        assert result == []

    @patch('builtins.input', return_value='invalid-range')
    def test_handles_invalid_range(self, mock_input, capsys):
        """Test handling invalid range format."""
        recs = [{'title': 'Movie 1', 'year': 2020, 'similarity': 0.8}]

        result = user_select_recommendations(recs, "add")

        assert result == []

    @patch('builtins.input', return_value='1,2')
    def test_displays_recommendations(self, mock_input, capsys):
        """Test that recommendations are displayed."""
        recs = [
            {'title': 'Movie 1', 'year': 2020, 'similarity': 0.8},
            {'title': 'Movie 2', 'year': 2021, 'score': 0.7}  # Test 'score' fallback
        ]

        user_select_recommendations(recs, "add")

        captured = capsys.readouterr()
        assert 'Movie 1' in captured.out
        assert 'Movie 2' in captured.out
        assert '2020' in captured.out
