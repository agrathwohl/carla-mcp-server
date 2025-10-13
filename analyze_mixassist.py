#!/usr/bin/env python3
"""
Analyze MixAssist dataset structure to understand the data format
"""
import pandas as pd
import json
import sys
from pathlib import Path

def analyze_parquet_file(file_path):
    """Analyze a parquet file and return sample data"""
    try:
        df = pd.read_parquet(file_path)

        print(f"=== Analysis of {file_path} ===")
        print(f"Shape: {df.shape}")
        print(f"Columns: {list(df.columns)}")
        print()

        print("=== Data Types ===")
        print(df.dtypes)
        print()

        print("=== Topic Distribution ===")
        if 'topic' in df.columns:
            print(df['topic'].value_counts())
        print()

        print("=== Sample Records ===")
        for i, row in df.head(3).iterrows():
            print(f"--- Record {i} ---")
            for col in df.columns:
                if col == 'input_history':
                    input_hist = row[col]
                    if input_hist is not None and hasattr(input_hist, '__len__'):
                        print(f"{col}: {type(input_hist)} with {len(input_hist)} items")
                        if len(input_hist) > 0:
                            print(f"  First item: {input_hist[0]}")
                    else:
                        print(f"{col}: {input_hist}")
                else:
                    value = row[col]
                    if isinstance(value, str) and len(value) > 100:
                        print(f"{col}: {value[:100]}...")
                    else:
                        print(f"{col}: {value}")
            print()

        return df

    except Exception as e:
        print(f"Error reading {file_path}: {e}")
        return None

def main():
    base_path = Path("/mnt/Media/MixAssist/data")

    for file_name in ["train-00000-of-00001.parquet", "test-00000-of-00001.parquet", "validation-00000-of-00001.parquet"]:
        file_path = base_path / file_name
        if file_path.exists():
            df = analyze_parquet_file(file_path)
            if df is not None and len(df) > 0:
                # Check audio file paths to understand directory structure
                print("=== Audio File Path Analysis ===")
                audio_files = set()
                for _, row in df.iterrows():
                    if row.get('audio_file'):
                        audio_files.add(row['audio_file'])
                    if row.get('input_history'):
                        for item in row['input_history']:
                            if isinstance(item, dict) and 'audio_file' in item:
                                audio_files.add(item['audio_file'])

                print(f"Unique audio file paths (first 10):")
                for audio_file in list(audio_files)[:10]:
                    print(f"  {audio_file}")
                print()

                break  # Just analyze one file for now

if __name__ == "__main__":
    main()