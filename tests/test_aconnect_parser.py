from __future__ import annotations

from pathlib import Path

from engine.adapters.aconnect_parser import parse_aconnect_output


FIXTURES = Path(__file__).parent / "fixtures"


def _read_fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_parse_aconnect_o_fixture_ports_are_discovered() -> None:
    entries = parse_aconnect_output(_read_fixture("aconnect_o_sample.txt"))

    assert ("20", "Cirklon", "0", "MIDI 1          ") in entries
    assert ("24", "USB MIDI Interface", "0", "USB MIDI Interface MIDI 1") in entries
    assert ("128", "RtMidiOut Client", "0", "GreenCRT Panic Out") in entries


def test_parse_aconnect_l_fixture_ignores_connection_detail_lines() -> None:
    entries = parse_aconnect_output(_read_fixture("aconnect_l_sample.txt"))

    assert ("129", "RtMidiIn Client", "0", "GreenCRT Monitor") in entries
    assert ("130", "RtMidiIn Client", "0", "Aux Monitor") in entries
    assert all("Connected From" not in port_name for _, _, _, port_name in entries)
    assert all("Connecting To" not in port_name for _, _, _, port_name in entries)


def test_parse_aconnect_output_returns_empty_list_for_blank_input() -> None:
    assert parse_aconnect_output("") == []
