"""DeepEval 数据集管理工具。"""
from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from deepeval.dataset import EvaluationDataset, Golden


def save_dataset(
    goldens: List[Golden],
    file_type: str = "csv",
    directory: str = "eval/datasets",
    file_name: str = "goldens",
) -> str:
    """将 Golden 列表保存为 CSV / JSON / JSONL 文件。

    Args:
        goldens: Golden 实例列表。
        file_type: 文件格式，支持 "csv"、"json"、"jsonl"。
        directory: 输出目录。
        file_name: 文件名（不含扩展名）。

    Returns:
        保存文件的完整路径。
    """
    out_dir = Path(directory)
    out_dir.mkdir(parents=True, exist_ok=True)
    ds = EvaluationDataset(goldens=goldens)
    return ds.save_as(file_type, str(out_dir), file_name)


def load_dataset(file_path: str) -> EvaluationDataset:
    """从文件加载 EvaluationDataset。

    Args:
        file_path: 数据集文件路径，支持 .csv / .json / .jsonl。

    Returns:
        加载后的 EvaluationDataset 实例。
    """
    p = Path(file_path)
    ds = EvaluationDataset()
    if p.suffix == ".csv":
        ds.add_test_cases_from_csv_file(str(p))
    elif p.suffix == ".json":
        ds.add_test_cases_from_json_file(str(p))
    elif p.suffix == ".jsonl":
        ds.add_goldens_from_jsonl_file(str(p))
    else:
        raise ValueError(f"不支持的文件格式：{p.suffix}（支持 .csv / .json / .jsonl）")
    return ds
