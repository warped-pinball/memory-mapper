"""Tests for memory_mapper.__main__ (CLI argument parsing)."""

import pytest

from memory_mapper.__main__ import build_parser


class TestBuildParser:
    def test_defaults(self):
        parser = build_parser()
        args = parser.parse_args([])
        assert args.group == "239.255.0.0"
        assert args.port == 2040
        assert args.highlight_duration == 3.0
        assert args.bytes_per_row == 16

    def test_custom_group(self):
        parser = build_parser()
        args = parser.parse_args(["--group", "239.1.2.3"])
        assert args.group == "239.1.2.3"

    def test_custom_port(self):
        parser = build_parser()
        args = parser.parse_args(["--port", "5000"])
        assert args.port == 5000

    def test_custom_highlight_duration(self):
        parser = build_parser()
        args = parser.parse_args(["--highlight-duration", "1.5"])
        assert args.highlight_duration == 1.5

    def test_custom_bytes_per_row(self):
        parser = build_parser()
        args = parser.parse_args(["--bytes-per-row", "8"])
        assert args.bytes_per_row == 8

    def test_invalid_port_type(self):
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["--port", "notanumber"])

    def test_invalid_highlight_duration_type(self):
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["--highlight-duration", "bad"])
