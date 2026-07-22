"""Tests for web/config_validate.py - field-level validation used before
any config screen writes to disk."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from web.config_validate import (
    validate_choice,
    validate_float,
    validate_int,
    validate_required,
    validate_url,
    validate_weights_sum,
)


class TestValidateUrl:
    def test_valid_http_url_passes(self):
        errors = {}
        validate_url('http://localhost:8989', 'f', errors)
        assert errors == {}

    def test_valid_https_url_passes(self):
        errors = {}
        validate_url('https://example.plex.direct:32400', 'f', errors)
        assert errors == {}

    def test_missing_scheme_fails(self):
        errors = {}
        validate_url('localhost:8989', 'f', errors)
        assert 'f' in errors

    def test_garbage_fails(self):
        errors = {}
        validate_url('not a url at all', 'f', errors)
        assert 'f' in errors

    def test_blank_not_required_is_fine(self):
        errors = {}
        validate_url('', 'f', errors, required=False)
        assert errors == {}

    def test_blank_required_fails(self):
        errors = {}
        validate_url('', 'f', errors, required=True)
        assert 'f' in errors


class TestValidateRequired:
    def test_present_passes(self):
        errors = {}
        validate_required('Movies', 'f', errors)
        assert errors == {}

    def test_blank_fails(self):
        errors = {}
        validate_required('   ', 'f', errors, label='Movie library')
        assert 'f' in errors
        assert 'Movie library' in errors['f']


class TestValidateChoice:
    def test_valid_choice_passes(self):
        errors = {}
        validate_choice('mapping', 'f', errors, ('mapping', 'per_user', 'combined'))
        assert errors == {}

    def test_invalid_choice_fails(self):
        errors = {}
        validate_choice('bogus', 'f', errors, ('mapping', 'per_user', 'combined'))
        assert 'f' in errors


class TestValidateFloat:
    def test_parses_valid_float(self):
        errors = {}
        assert validate_float('0.25', 'f', errors) == 0.25
        assert errors == {}

    def test_non_numeric_records_error_returns_none(self):
        errors = {}
        assert validate_float('not-a-number', 'f', errors) is None
        assert 'f' in errors

    def test_below_range_records_error(self):
        errors = {}
        validate_float('-1', 'f', errors, lo=0, hi=1)
        assert 'f' in errors

    def test_above_range_records_error(self):
        errors = {}
        validate_float('1.5', 'f', errors, lo=0, hi=1)
        assert 'f' in errors


class TestValidateInt:
    def test_parses_valid_int(self):
        errors = {}
        assert validate_int('50', 'f', errors) == 50
        assert errors == {}

    def test_non_numeric_records_error_returns_none(self):
        errors = {}
        assert validate_int('abc', 'f', errors) is None
        assert 'f' in errors

    def test_out_of_range(self):
        errors = {}
        validate_int('500', 'f', errors, lo=0, hi=100)
        assert 'f' in errors


class TestValidateWeightsSum:
    def test_sum_to_one_passes(self):
        errors = {}
        validate_weights_sum({'a': 0.25, 'b': 0.25, 'c': 0.5}, 'weights', errors)
        assert errors == {}

    def test_sum_not_one_fails(self):
        errors = {}
        validate_weights_sum({'a': 0.5, 'b': 0.6}, 'weights', errors)
        assert 'weights' in errors
        assert '1.1' in errors['weights']

    def test_within_tolerance_passes(self):
        errors = {}
        validate_weights_sum({'a': 0.3333333, 'b': 0.3333333, 'c': 0.3333334}, 'weights', errors)
        assert errors == {}
