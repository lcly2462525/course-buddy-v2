"""Offline regression tests. All transport/model calls use synthetic fixtures."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, PropertyMock, patch

from course_buddy_v2 import notes


LLM = notes._get_llm_config({"api_key": "test-not-a-real-key", "use_env_proxy": False})


def event(content=None, finish=None):
    return b'data: ' + json.dumps({"choices": [{"delta": {"content": content}, "finish_reason": finish}]}).encode()


class StreamIntegrityTests(unittest.TestCase):
    def call(self, lines):
        response = Mock(status_code=200)
        response.iter_lines.return_value = iter(lines)
        session = Mock()
        session.post.return_value = response
        result = notes._stream_chat_completion(session, "https://example.invalid", {}, {}, 1)
        response.close.assert_called_once()
        return result

    def test_clean_stop(self):
        self.assertEqual(self.call([event("完整"), event(finish="stop"), b'data: [DONE]']), ("完整", "stop", None))

    def test_iterator_exception_keeps_error_and_closes_response(self):
        def broken():
            yield event("半句")
            raise RuntimeError("simulated disconnect")
        content, finish, error = self.call(broken())
        self.assertEqual(content, "半句")
        self.assertIsNone(finish)
        self.assertIn("中断", error)

    def test_no_content(self):
        self.assertIsNone(self.call([event(finish="stop")])[0])

    def test_token_limit_exhaustion_has_diagnostic(self):
        with patch.object(notes, '_stream_chat_completion', return_value=("半句", "length", None)):
            result, error = notes._call_llm_result("测试", LLM)
        self.assertIsNone(result)
        self.assertIn("length", error)

    def test_http_error_body_failure_returns_diagnostic(self):
        response = Mock(status_code=503)
        type(response).text = PropertyMock(side_effect=RuntimeError('disconnect'))
        session = Mock()
        session.post.return_value = response
        content, finish, error = notes._stream_chat_completion(session, 'https://example.invalid', {}, {}, 1)
        self.assertIsNone(content)
        self.assertIn('HTTP 503', error)
        response.close.assert_called_once()

    def test_silent_eof_is_incomplete(self):
        self.assertIsNotNone(self.call([event("半句")])[2])

    def test_done_without_finish_reason_is_incomplete(self):
        self.assertIsNotNone(self.call([event("半句"), b'data: [DONE]'])[2])

    def test_malformed_event_is_not_silently_ignored(self):
        self.assertIsNotNone(self.call([event("前半"), b'data: {bad', event(finish="stop")])[2])

    def test_api_error_event_is_not_success(self):
        self.assertIsNotNone(self.call([event("半句"), b'data: {"error":{"message":"failed"}}', event(finish="stop")])[2])

    def test_partial_content_with_transport_error_is_not_success(self):
        with patch.object(notes, '_stream_chat_completion', return_value=("半句", None, "流式读取中断")):
            result, error = notes._call_llm_result("测试", LLM)
        self.assertIsNone(result)
        self.assertIn("中断", error)

    def test_length_at_cap_is_not_success(self):
        with patch.object(notes, '_stream_chat_completion', return_value=("半句", "length", None)):
            result, error = notes._call_llm_result("测试", LLM, max_tokens=65000)
        self.assertIsNone(result)
        self.assertIn("length", error)

    def test_retries_can_recover(self):
        with patch.object(notes, '_stream_chat_completion', side_effect=[("半句", None, "断流"), ("完整", "stop", None)]):
            self.assertEqual(notes._call_llm_result("测试", dict(LLM, retries=2)), ("完整", None))

    def test_content_filter_is_not_success(self):
        with patch.object(notes, '_stream_chat_completion', return_value=("半句", "content_filter", None)):
            self.assertIsNone(notes._call_llm_result("测试", LLM)[0])


class NoteCoverageTests(unittest.TestCase):
    def run_notes(self, handler, times=(0, 60, 120), enabled=True):
        with tempfile.TemporaryDirectory() as directory:
            transcript = Path(directory) / '2026-01-01-demo.json'
            summary = Path(directory) / 'summary.json'
            transcript.write_text(json.dumps({"data": {"afterAssemblyList": [{"bg": t * 1000, "res": f"模拟课堂第{i}段"} for i, t in enumerate(times)]}}))
            summary.write_text('{}')
            progress = []
            with patch.object(notes, '_call_llm_result', side_effect=handler):
                result = notes.summarize_transcript_files(transcript_path=str(transcript), summary_path=str(summary), course_name="模拟课", llm_cfg=dict(LLM, notes_chunk_minutes=1, enabled=enabled), progress=progress.append)
            return result, progress

    def test_missing_chunk_keeps_position_and_warning(self):
        def handler(prompt, *args, **kwargs):
            if '分片 2/3' in prompt:
                return None, '模拟断流'
            if '分片 1/3' in prompt:
                return '第一段成功', None
            if '分片 3/3' in prompt:
                return '第三段成功', None
            return '模型误称完整', None
        result, progress = self.run_notes(handler)
        self.assertIn('笔记不完整', result)
        self.assertIn('分片 2/3', result)
        self.assertIn('00:01:00', result)
        self.assertLess(result.index('第一段成功'), result.index('缺失分片'))
        self.assertLess(result.index('缺失分片'), result.index('第三段成功'))
        self.assertNotIn('笔记生成完成。', progress)
        self.assertNotIn('模型误称完整', result)

    def test_all_failed_is_labeled_fallback(self):
        result, progress = self.run_notes(lambda *a, **k: (None, '模拟失败'))
        self.assertIn('笔记不完整', result)
        self.assertIn('0/3', result)
        self.assertIn('平台摘要', result)
        self.assertNotIn('未配置可用 LLM', result)
        self.assertNotIn('笔记生成完成。', progress)

    def test_single_failure_is_labeled(self):
        result, progress = self.run_notes(lambda *a, **k: (None, '模拟失败'), times=(0,))
        self.assertIn('笔记不完整', result)
        self.assertNotIn('笔记生成完成。', progress)

    def test_success_is_not_marked_incomplete(self):
        result, progress = self.run_notes(lambda *a, **k: ('成功笔记', None))
        self.assertNotIn('笔记不完整', result)
        self.assertIn('笔记生成完成。', progress)

    def test_worker_exception_preserves_other_chunks(self):
        def handler(prompt, *args, **kwargs):
            if '分片 2/3' in prompt:
                raise RuntimeError('synthetic worker failure')
            return '有效分片', None
        result, progress = self.run_notes(handler)
        self.assertIn('2/3 个分片', result)
        self.assertEqual(result.count('有效分片'), 2)
        self.assertNotIn('笔记生成完成。', progress)

    def test_failed_merge_preserves_all_source_chunks(self):
        def handler(prompt, *args, **kwargs):
            if prompt.startswith('整理课堂转录分片'):
                return prompt.split('（', 1)[0], None
            return None, '模拟合并失败'
        result, progress = self.run_notes(handler)
        for i in (1, 2, 3):
            self.assertIn(f'分片 {i}/3', result)
        self.assertNotIn('笔记不完整', result)

    def test_single_success(self):
        result, progress = self.run_notes(lambda *a, **k: ('单段成功', None), times=(0,))
        self.assertEqual(result, '单段成功')
        self.assertIn('笔记生成完成。', progress)

    def test_disabled_model_does_not_claim_full_notes(self):
        result, progress = self.run_notes(lambda *a, **k: self.fail('model called'), enabled=False)
        self.assertIn('转录摘录', result)
        self.assertNotIn('笔记生成完成。', progress)


if __name__ == '__main__':
    unittest.main()
