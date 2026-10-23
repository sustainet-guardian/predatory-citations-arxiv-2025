#!/usr/bin/env python3
"""Extracts the .bib files from the arXiv source archives (YYMM/NNNNN.tar.gz under -d, default: current directory; only
month directories whose name starts with --year-prefix, default 25, are read). The directory layout is described in
post-processing/README.md.
Writes BIB/YYMM/NNNNN-X.bib (X = running number of the .bib file in the archive) and, when the .tex files cite keys defined in
that .bib file, BIB/YYMM/NNNNN-X-cited.csv (columns citation_key, count). Logs one row per archive (status, number of .bib and
.tex files, number of entries, number of cited entries) to the CSV given by -o (default arxiv_bib_extraction.csv). The entry
count is the number of distinct keys in each .bib file (a key that occurs twice counts once), summed over the .bib files of the
archive; the keys are the matches of the pattern @type{key, which also matches @string, @comment and @preamble. Archives
already logged with status success (when their .bib files are on disk), no_bib_files, not_gzip or not_tar are skipped, the
others are processed again. The archives are the e-prints of arXiv's bulk data; the licence of each e-print decides whether
the extracted .bib content may be redistributed; it is not redistributed here.

Usage:
  python3 arxiv_bib_extractor.py [-d /path/to/arxiv] [-o extraction.csv] [--year-prefix 25]
"""

import sys
import csv
import gzip
import tarfile
import argparse
import subprocess
import re
from pathlib import Path
from datetime import datetime
from collections import defaultdict, Counter


class BibExtractor:
    """Handles BibTeX extraction from arXiv tar.gz files."""

    def __init__(self, base_dir, csv_log_file, year_prefix='25'):
        self.base_dir = Path(base_dir)
        self.year_prefix = year_prefix
        self.bib_base_dir = self.base_dir / "BIB"
        self.csv_log_file = Path(csv_log_file)
        self.fieldnames = [
            'arxiv_id',
            'sequence_id',
            'year_month',
            'tar_gz_path',
            'status',
            'error_message',
            'file_type',
            'num_bib_files',
            'bib_files_extracted',
            'has_tex_files',
            'num_tex_files',
            'num_total_bib_entries',
            'num_cited_entries',
            'processed_timestamp'
        ]
        self.processed_cache = {}
        self._load_processed_cache()

    def _load_processed_cache(self):
        """Load already processed files from CSV into memory."""
        if not self.csv_log_file.exists():
            with open(self.csv_log_file, 'w', newline='', encoding='utf-8') as f:
                writer = csv.DictWriter(f, fieldnames=self.fieldnames)
                writer.writeheader()
            return

        with open(self.csv_log_file, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                key = (row['year_month'], row['sequence_id'])
                self.processed_cache[key] = row

    def _log_to_csv(self, record):
        """Append a record to the CSV log file."""
        with open(self.csv_log_file, 'a', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=self.fieldnames)
            writer.writerow(record)

        key = (record['year_month'], record['sequence_id'])
        self.processed_cache[key] = record

    def _check_idempotency(self, year_month, sequence_id):
        """
        Check if files on disk match CSV records.

        Returns:
            tuple: (is_valid, issues_list)
        """
        issues = []
        key = (year_month, sequence_id)

        csv_record = self.processed_cache.get(key)

        bib_dir = self.bib_base_dir / year_month
        existing_files = []
        if bib_dir.exists():
            pattern = f"{int(sequence_id):05d}-*.bib"
            existing_files = sorted(bib_dir.glob(pattern))

        if csv_record and csv_record['status'] == 'success':
            expected_count = int(csv_record['num_bib_files'])

            if len(existing_files) != expected_count:
                issues.append(f"CSV says {expected_count} files, found {len(existing_files)} on disk")

        if existing_files and not csv_record:
            issues.append(f"Found {len(existing_files)} .bib files on disk but no CSV record")

        return len(issues) == 0, issues

    def is_already_processed(self, year_month, sequence_id):
        """
        Check if file is already processed based on CSV and disk files.

        Returns:
            tuple: (should_skip, reason)
        """
        key = (year_month, sequence_id)
        csv_record = self.processed_cache.get(key)

        bib_dir = self.bib_base_dir / year_month
        pattern = f"{int(sequence_id):05d}-*.bib"
        existing_files = list(bib_dir.glob(pattern)) if bib_dir.exists() else []

        if csv_record and csv_record['status'] == 'success' and existing_files:
            expected_count = int(csv_record['num_bib_files'])
            if len(existing_files) == expected_count:
                return True, f"Already processed ({expected_count} .bib files)"

        if csv_record and csv_record['status'] in ['no_bib_files', 'not_gzip', 'not_tar']:
            return True, f"Previously processed: {csv_record['status']}"

        return False, None

    def validate_gzip(self, file_path):
        """Validate that file is a valid gzip file."""
        try:
            with gzip.open(file_path, 'rb') as f:
                f.read(1)
            return True
        except (gzip.BadGzipFile, OSError):
            return False

    def validate_tar(self, file_path):
        """Validate that decompressed content is a valid tar file."""
        try:
            with gzip.open(file_path, 'rb') as gz:
                # Read a small portion to check tar magic bytes
                header = gz.read(512)
                if len(header) < 512:
                    return False
                # Check for tar magic bytes at offset 257
                # Should be "ustar" (POSIX tar)
                return header[257:262] in [b'ustar', b'ustar ']
        except Exception:
            return False

    def detect_file_type(self, file_path):
        """Detect actual file type using /usr/bin/file command."""
        try:
            result = subprocess.run(
                ['/usr/bin/file', '-b', str(file_path)],
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode == 0:
                return result.stdout.strip()
            return 'unknown'
        except Exception as e:
            return f'error: {str(e)}'

    def parse_bib_entries(self, bib_content):
        """
        Extract all entry keys from BibTeX content.

        Returns:
            set: Set of citation keys found in the .bib file
        """
        keys = set()
        # Match @article{key, @book{key, etc.
        # Pattern: @type{key,
        pattern = r'@\w+\s*\{\s*([^,\s]+)'

        try:
            if isinstance(bib_content, bytes):
                try:
                    text = bib_content.decode('utf-8')
                except UnicodeDecodeError:
                    text = bib_content.decode('latin-1', errors='ignore')
            else:
                text = bib_content

            matches = re.finditer(pattern, text, re.IGNORECASE | re.MULTILINE)
            for match in matches:
                key = match.group(1).strip()
                if key:
                    keys.add(key)
        except Exception:
            pass

        return keys

    def extract_citations_from_tex(self, tex_content):
        """Count the keys in \\cite-type commands (\\cite, \\citep, \\citet, with optional arguments, comma-separated keys)."""
        citations = Counter()

        try:
            if isinstance(tex_content, bytes):
                try:
                    text = tex_content.decode('utf-8')
                except UnicodeDecodeError:
                    text = tex_content.decode('latin-1', errors='ignore')
            else:
                text = tex_content

            # Pattern to match various cite commands
            # Matches: \cite, \citep, \citet, \citealt, etc.
            # With optional arguments: \cite[...]{keys}
            # Keys can be comma-separated
            pattern = r'\\cite[a-z]*\s*(?:\[[^\]]*\])?\s*\{([^}]+)\}'

            matches = re.finditer(pattern, text, re.IGNORECASE)
            for match in matches:
                keys_str = match.group(1)
                keys = [k.strip() for k in keys_str.split(',')]
                for key in keys:
                    if key:
                        citations[key] += 1

        except Exception:
            pass

        return citations

    def create_cited_csv(self, output_path, citations):
        """
        Create a CSV file with citation keys and counts.

        Args:
            output_path: Path for the -cited.csv file
            citations: Counter object with citation keys and counts
        """
        try:
            with open(output_path, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow(['citation_key', 'count'])
                for key, count in sorted(citations.items()):
                    writer.writerow([key, count])
        except Exception as e:
            print(f"      [WARNING] Could not write cited CSV: {e}")

    def extract_bib_files(self, tar_gz_path, year_month, sequence_id):
        """
        Extract all .bib files from tar.gz archive and analyze citations.

        Returns:
            dict: {
                'status': str,
                'error_message': str,
                'bib_files': list,
                'has_tex_files': bool,
                'num_tex_files': int,
                'total_bib_entries': int,
                'cited_entries': int
            }
        """
        result = {
            'status': 'error',
            'error_message': '',
            'bib_files': [],
            'has_tex_files': False,
            'num_tex_files': 0,
            'total_bib_entries': 0,
            'cited_entries': 0
        }

        try:
            with tarfile.open(tar_gz_path, 'r:gz') as tar:
                members = tar.getmembers()

                bib_members = [m for m in members if m.name.lower().endswith('.bib') and m.isfile()]
                tex_members = [m for m in members if m.name.lower().endswith('.tex') and m.isfile()]

                result['has_tex_files'] = len(tex_members) > 0
                result['num_tex_files'] = len(tex_members)

                if not bib_members:
                    result['status'] = 'no_bib_files'
                    result['error_message'] = 'No .bib files found in archive'
                    return result

                output_dir = self.bib_base_dir / year_month
                output_dir.mkdir(parents=True, exist_ok=True)

                all_citations = Counter()
                if tex_members:
                    for tex_member in tex_members:
                        try:
                            tex_content = tar.extractfile(tex_member).read()
                            citations = self.extract_citations_from_tex(tex_content)
                            all_citations.update(citations)
                        except Exception:
                            pass

                for idx, member in enumerate(bib_members, start=1):
                    try:
                        content = tar.extractfile(member).read()

                        # Generate output filename: NNNNN-X.bib
                        output_filename = f"{int(sequence_id):05d}-{idx}.bib"
                        output_path = output_dir / output_filename

                        with open(output_path, 'wb') as f:
                            f.write(content)

                        result['bib_files'].append(output_filename)

                        bib_entries = self.parse_bib_entries(content)
                        result['total_bib_entries'] += len(bib_entries)

                        if all_citations:
                            cited_in_this_bib = {k: v for k, v in all_citations.items()
                                               if k in bib_entries}

                            if cited_in_this_bib:
                                cited_csv_filename = f"{int(sequence_id):05d}-{idx}-cited.csv"
                                cited_csv_path = output_dir / cited_csv_filename
                                self.create_cited_csv(cited_csv_path, cited_in_this_bib)
                                result['cited_entries'] += len(cited_in_this_bib)

                    except Exception as e:
                        print(f"      [WARNING] Error processing bib file {member.name}: {e}")

                result['status'] = 'success'
                return result

        except tarfile.TarError as e:
            result['status'] = 'error'
            result['error_message'] = f"Tar extraction error: {str(e)}"
            return result
        except Exception as e:
            result['status'] = 'error'
            result['error_message'] = f"Unexpected error: {str(e)}"
            return result

    def process_file(self, tar_gz_path, year_month, sequence_id):
        """
        Process a single tar.gz file.

        Returns:
            dict: Record to log to CSV
        """
        arxiv_id = f"{year_month}.{int(sequence_id):05d}"

        should_skip, reason = self.is_already_processed(year_month, sequence_id)
        if should_skip:
            print(f"  [SKIP] {arxiv_id} - {reason}")
            return None

        print(f"  [PROCESS] {arxiv_id}")

        record = {
            'arxiv_id': arxiv_id,
            'sequence_id': f"{int(sequence_id):05d}",
            'year_month': year_month,
            'tar_gz_path': str(tar_gz_path.relative_to(self.base_dir)),
            'status': 'error',
            'error_message': '',
            'file_type': '',
            'num_bib_files': 0,
            'bib_files_extracted': '',
            'has_tex_files': False,
            'num_tex_files': 0,
            'num_total_bib_entries': 0,
            'num_cited_entries': 0,
            'processed_timestamp': datetime.now().isoformat()
        }

        if not self.validate_gzip(tar_gz_path):
            record['status'] = 'not_gzip'
            record['error_message'] = 'Not a valid gzip file'
            file_type = self.detect_file_type(tar_gz_path)
            record['file_type'] = file_type
            print(f"    [ERROR] Not a valid gzip file - detected as: {file_type}")
            self._log_to_csv(record)
            return record

        record['file_type'] = 'gzip'

        if not self.validate_tar(tar_gz_path):
            record['status'] = 'not_tar'
            record['error_message'] = 'Not a valid tar archive after decompression'
            print("    [ERROR] Not a valid tar archive")
            self._log_to_csv(record)
            return record

        extraction_result = self.extract_bib_files(
            tar_gz_path, year_month, sequence_id
        )

        record['status'] = extraction_result['status']
        record['error_message'] = extraction_result['error_message']
        record['num_bib_files'] = len(extraction_result['bib_files'])
        record['bib_files_extracted'] = ', '.join(extraction_result['bib_files'])
        record['has_tex_files'] = extraction_result['has_tex_files']
        record['num_tex_files'] = extraction_result['num_tex_files']
        record['num_total_bib_entries'] = extraction_result['total_bib_entries']
        record['num_cited_entries'] = extraction_result['cited_entries']

        if record['status'] == 'success':
            msg = f"Extracted {record['num_bib_files']} .bib file(s)"
            if record['num_total_bib_entries'] > 0:
                msg += f", {record['num_total_bib_entries']} total entries"
            if record['num_cited_entries'] > 0:
                msg += f", {record['num_cited_entries']} cited"
            if not record['has_tex_files']:
                msg += " (no .tex files)"
            print(f"    [SUCCESS] {msg}")
        elif record['status'] == 'no_bib_files':
            print("    [INFO] No .bib files found")
        else:
            print(f"    [ERROR] {record['error_message']}")

        self._log_to_csv(record)

        return record

    def scan_and_process(self):
        """Scan all month directories starting with the year prefix and process tar.gz files."""
        print("=" * 70)
        print("arXiv BibTeX Extractor")
        print("=" * 70)
        print(f"Base Directory: {self.base_dir}")
        print(f"BIB Output: {self.bib_base_dir}")
        print(f"CSV Log: {self.csv_log_file}")
        print("=" * 70)

        month_dirs = sorted([d for d in self.base_dir.iterdir()
                           if d.is_dir() and d.name.startswith(self.year_prefix) and len(d.name) == 4])

        if not month_dirs:
            print(f"[WARNING] No {self.year_prefix}XX directories found")
            return

        print(f"Found {len(month_dirs)} month directories: {', '.join(d.name for d in month_dirs)}")
        print()

        total_files = 0
        processed = 0
        skipped = 0
        success = 0
        no_bib = 0
        errors = 0

        for month_dir in month_dirs:
            year_month = month_dir.name
            print(f"Processing {year_month}/")

            tar_files = sorted(month_dir.glob("*.tar.gz"))

            if not tar_files:
                print("  No .tar.gz files found")
                continue

            total_files += len(tar_files)

            for tar_file in tar_files:
                sequence_id = tar_file.stem.replace('.tar', '')

                record = self.process_file(tar_file, year_month, sequence_id)

                if record is None:
                    skipped += 1
                else:
                    processed += 1
                    if record['status'] == 'success':
                        success += 1
                    elif record['status'] == 'no_bib_files':
                        no_bib += 1
                    else:
                        errors += 1

            print()

        print("=" * 70)
        print("EXTRACTION COMPLETE")
        print("=" * 70)
        print(f"Total .tar.gz files:     {total_files}")
        print(f"Skipped (already done):  {skipped}")
        print(f"Processed:               {processed}")
        print(f"  - Success:             {success}")
        print(f"  - No .bib files:       {no_bib}")
        print(f"  - Errors:              {errors}")
        print("=" * 70)
        print(f"CSV log: {self.csv_log_file}")
        print(f"BIB files: {self.bib_base_dir}/")

        self.verify_idempotency()

    def verify_idempotency(self):
        """Verify that CSV records match files on disk."""
        print("\n" + "=" * 70)
        print("IDEMPOTENCY VERIFICATION")
        print("=" * 70)

        issues_found = 0

        for (year_month, sequence_id), record in self.processed_cache.items():
            if record['status'] != 'success':
                continue

            is_valid, issues = self._check_idempotency(year_month, sequence_id)

            if not is_valid:
                issues_found += 1
                arxiv_id = f"{year_month}.{sequence_id}"
                print(f"[MISMATCH] {arxiv_id}:")
                for issue in issues:
                    print(f"  - {issue}")

        for month_dir in sorted(self.bib_base_dir.glob(f"{self.year_prefix}*")):
            if not month_dir.is_dir():
                continue

            year_month = month_dir.name
            bib_files = sorted(month_dir.glob("*.bib"))

            by_seq = defaultdict(list)
            for bib_file in bib_files:
                parts = bib_file.stem.split('-')
                if len(parts) >= 2:
                    seq_id = parts[0]
                    by_seq[seq_id].append(bib_file.name)

            for seq_id, files in by_seq.items():
                key = (year_month, seq_id)
                if key not in self.processed_cache:
                    issues_found += 1
                    print(f"[ORPHAN] {year_month}.{seq_id}: {len(files)} .bib files on disk but no CSV record")

        if issues_found == 0:
            print("[OK] All CSV records match files on disk")
        else:
            print(f"\n[WARNING] Found {issues_found} idempotency issue(s)")

        print("=" * 70)


def main():
    parser = argparse.ArgumentParser(
        description='Extract BibTeX files from arXiv tar.gz archives',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )

    parser.add_argument(
        '-d', '--directory',
        default='.',
        help='Base directory containing the month folders (default: current directory)'
    )
    parser.add_argument(
        '-o', '--output',
        default='arxiv_bib_extraction.csv',
        help='CSV log file (default: arxiv_bib_extraction.csv, appends if exists)'
    )
    parser.add_argument(
        '--year-prefix',
        default='25',
        help='Only month folders whose name starts with this prefix are read (default: 25)'
    )

    args = parser.parse_args()

    extractor = BibExtractor(args.directory, args.output, args.year_prefix)

    extractor.scan_and_process()


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n[INTERRUPTED] Extraction stopped by user.")
        sys.exit(130)
    except Exception as e:
        print(f"\n[FATAL ERROR] {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)
