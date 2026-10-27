"""A Windows folder name is not a credential.

Seen in two demo runs: a script the agent wrote held the project's own folder, `E:\\agent8088_demo\\batches\\pokemon_v19_a8088_or_27_20_2\\runs\\run1`.
The scan split it at the backslashes, took the batch folder name (28 characters with digits) for an opaque key, and asked the agent twice to
remove a "credential" that was a path."""
import pytest

from agent8088 import secret_scan

BATCH = "pokemon_v19_a8088_or_27_20_2"


@pytest.mark.parametrize("line", [
    rf"E:\agent8088_demo\batches\{BATCH}\runs\run1\work\artifacts",
    rf'BASE = "E:\\agent8088_demo\\batches\\{BATCH}\\runs\\run1"',
    rf"cd C:\Users\imroze.aslam\projects\{BATCH}",
    rf"\\fileserver\share\{BATCH}\notes",
    rf"E:\{BATCH}",
    rf"python E:\work\{BATCH}\build.py --out E:\work\{BATCH}\dist",
])
def test_a_windows_path_with_a_batch_folder_name_is_not_a_credential(line):
    assert secret_scan.find_secrets(line) == []


def test_the_same_name_outside_a_path_is_still_judged_as_before():
    assert secret_scan.find_secrets(BATCH)


@pytest.mark.parametrize("line", [
    "token = 'q8Zr1xKp3LmN7vB2cD5fG9hJ0aS4wE6y'",
    rf"E:\work\run1 key=q8Zr1xKp3LmN7vB2cD5fG9hJ0aS4wE6y",
    rf"open('E:\\work\\x.txt'); API_KEY='q8Zr1xKp3LmN7vB2cD5fG9hJ0aS4wE6y'",
    "sk-proj-abcdefghijklmnopqrstuvwx0123456789",
])
def test_a_real_key_beside_a_path_is_still_found(line):
    assert secret_scan.find_secrets(line)


def test_a_key_with_one_backslash_is_not_taken_for_a_path():
    assert secret_scan.find_secrets(r"q8Zr1xKp3LmN7v\B2cD5fG9hJ0aS4wE6y")
