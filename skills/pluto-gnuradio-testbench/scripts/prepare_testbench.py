"""Copy bundled testbench into a new directory; no hardware access or overwrites."""
import argparse
import shutil
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--destination', required=True, type=Path)
    args = parser.parse_args()
    source = Path(__file__).resolve().parents[1] / 'assets' / 'testbench'
    target = args.destination.expanduser().resolve()
    if target.exists():
        parser.error('Destination already exists; use it directly or choose a new directory')
    if not (source / 'radio_test.py').is_file():
        parser.error('Bundled testbench missing')
    shutil.copytree(source, target)
    print('Prepared testbench:', target)
    print('No hardware was accessed. Check interpreter paths and device identity before running.')


if __name__ == '__main__':
    main()
