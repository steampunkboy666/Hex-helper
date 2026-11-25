"""验证真实数据源降级和断网缓存，主榜单故障用受控异常模拟。"""
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import sources
from data_source import Data
import haidou


def unavailable(*args, **kwargs):
    raise TimeoutError("模拟超时")


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as root:
        data = Data(root)
        with patch.object(sources, "aramgg_summary", unavailable), patch.object(sources, "aramgg_champion", unavailable):
            data.load()
            assert data.meta["source"] == "haidou"
            detail = data.ensure_detail("77")
            assert detail["aug_source"] == "haidou"
            assert detail["itemStats"]["source"] == "haidou"
            assert len(data.top_items("77")) == 3
            print("PASS 真实海斗榜", data.meta["version"], data.meta["total"], "heroes")
            with patch.object(haidou.Haidou, "summary", unavailable):
                data.load(force=True)
                assert data.meta["source"] == "opgg"
                assert data.summary("77")["winRate"] is None
                print("PASS OP.GG 最后兜底")
                with patch.object(sources, "opgg_summary", unavailable):
                    data.load(force=True)
                    assert data.meta["source"] == "opgg"
                    print("PASS 三源离线时读已保存缓存")
                    with tempfile.TemporaryDirectory() as empty:
                        try:
                            Data(empty).load(force=True)
                        except RuntimeError:
                            print("PASS 无缓存时给出错误")
                        else:
                            raise AssertionError("无缓存仍报告有数据")
