"""Unit tests for skylet log_lib."""

import io
from io import StringIO
import subprocess
import tempfile
import unittest
from unittest import mock

from sky.skylet import log_lib


class _BoundedReadlineStream(io.BytesIO):

    def readline(self, size: int = -1) -> bytes:
        if size <= 0:
            raise AssertionError('log reads must have a fixed size limit')
        return super().readline(size)


class TestHandleIoStream(unittest.TestCase):
    """Test cases for bounded subprocess stream processing."""

    def test_long_line_is_chunked_without_retaining_output(self):
        payload = b'x' * (log_lib.DEFAULT_LOG_CHUNK_SIZE * 2 + 1)
        stream = _BoundedReadlineStream(payload)
        with tempfile.NamedTemporaryFile(suffix='.log') as log_file:
            args = log_lib._ProcessingArgs(  # pylint: disable=protected-access
                log_file.name,
                stream_logs=False,
                capture_output=False)

            output = log_lib._handle_io_stream(  # pylint: disable=protected-access
                stream, StringIO(), args)

            self.assertEqual(output, '')
            with open(log_file.name, 'rb') as persisted_log:
                self.assertEqual(persisted_log.read(), payload)

    def test_requested_output_is_returned(self):
        payload = b'hello\n'
        with tempfile.NamedTemporaryFile(suffix='.log') as log_file:
            args = log_lib._ProcessingArgs(  # pylint: disable=protected-access
                log_file.name,
                stream_logs=False,
                capture_output=True)

            output = log_lib._handle_io_stream(  # pylint: disable=protected-access
                _BoundedReadlineStream(payload), StringIO(), args)

        self.assertEqual(output, payload.decode())


class TestFollowJobLogs(unittest.TestCase):

    def test_unterminated_line_is_yielded_in_bounded_chunks(self):
        payload = 'x' * (log_lib.DEFAULT_LOG_CHUNK_SIZE * 2) + '\n'
        with mock.patch.object(log_lib.job_lib,
                               'get_status_no_lock',
                               return_value=log_lib.job_lib.JobStatus.RUNNING):
            chunks = log_lib._follow_job_logs(  # pylint: disable=protected-access
                StringIO(payload),
                job_id=1,
                start_streaming=True)

            first_chunk = next(chunks)

        self.assertEqual(len(first_chunk), log_lib.DEFAULT_LOG_CHUNK_SIZE)
        self.assertEqual(first_chunk, 'x' * log_lib.DEFAULT_LOG_CHUNK_SIZE)


class TestLogBuffer(unittest.TestCase):
    """Test cases for LogBuffer class."""

    def test_initialization(self):
        """Test buffer initializes with correct defaults."""
        buffer = log_lib.LogBuffer()

        self.assertEqual(buffer.max_chars, log_lib.DEFAULT_LOG_CHUNK_SIZE)
        self.assertIsInstance(buffer._buffer, StringIO)
        self.assertEqual(buffer._buffer.getvalue(), '')

    def test_custom_parameters(self):
        """Test buffer initializes with custom parameters."""
        buffer = log_lib.LogBuffer(max_chars=1024)
        self.assertEqual(buffer.max_chars, 1024)

    def test_write_basic(self):
        """Test adding a single line to buffer."""
        buffer = log_lib.LogBuffer(max_chars=100)

        string = "Hello world\n"
        should_flush = buffer.write(string)

        self.assertFalse(should_flush)
        self.assertEqual(buffer._buffer.tell(), len(string))
        self.assertEqual(buffer._buffer.getvalue(), string)

    def test_write_triggers_size_flush(self):
        """Test that buffer flushes when size limit is reached."""
        buffer = log_lib.LogBuffer(max_chars=10)

        # Add a line that exceeds the size limit
        string = "This is a very long line that exceeds the buffer size\n"
        should_flush = buffer.write(string)

        self.assertTrue(should_flush)
        self.assertEqual(buffer._buffer.tell(), len(string))

    def test_flush_basic(self):
        """Test getting chunk from buffer."""
        buffer = log_lib.LogBuffer()

        buffer.write("Line 1\n")
        buffer.write("Line 2\n")
        buffer.write("Line 3\n")

        chunk = buffer.flush()

        self.assertEqual(chunk, "Line 1\nLine 2\nLine 3\n")
        self.assertEqual(buffer._buffer.tell(), 0)

    def test_flush_empty(self):
        """Test getting chunk from empty buffer."""
        buffer = log_lib.LogBuffer()

        chunk = buffer.flush()

        self.assertEqual(chunk, "")

    def test_unicode_characters(self):
        """Test buffer handles unicode characters correctly."""
        buffer = log_lib.LogBuffer()

        unicode_line = "Hello 🌍\n"
        buffer.write(unicode_line)

        # _buffer.tell() counts the number of characters,
        # not the number of bytes:
        # >>> len(unicode_line)
        # 8
        # >>> len(unicode_line.encode('utf-8'))
        # 11
        #
        # This is fine because our default chunk size is well below the
        # default grpc.max_receive_message_length which is 4MB.
        self.assertEqual(buffer._buffer.tell(), len(unicode_line))

        chunk = buffer.flush()
        self.assertEqual(chunk, unicode_line)

    def test_reset_after_flush(self):
        """Test that buffer is properly reset after getting chunk."""
        buffer = log_lib.LogBuffer()

        buffer.write("Line 1\n")
        buffer.write("Line 2\n")

        # Get chunk should reset everything
        chunk = buffer.flush()

        self.assertEqual(chunk, "Line 1\nLine 2\n")
        self.assertEqual(buffer._buffer.tell(), 0)


class TestRunWithLogTimeout(unittest.TestCase):
    """Test cases for run_with_log timeout functionality."""

    def test_process_stream_timeout_exceeded(self):
        """Test that timeout works with process_stream=True."""
        with tempfile.NamedTemporaryFile(suffix='.log', delete=False) as f:
            log_path = f.name

        # Command that sleeps longer than timeout
        cmd = ['sleep', '10']
        with self.assertRaises(subprocess.TimeoutExpired):
            log_lib.run_with_log(
                cmd,
                log_path,
                process_stream=True,
                timeout=1,
            )

    def test_process_stream_timeout_not_exceeded(self):
        """Test normal completion with process_stream=True and timeout set."""
        with tempfile.NamedTemporaryFile(suffix='.log', delete=False) as f:
            log_path = f.name

        # Command that completes quickly
        cmd = ['echo', 'hello']
        returncode = log_lib.run_with_log(
            cmd,
            log_path,
            process_stream=True,
            timeout=10,
        )
        self.assertEqual(returncode, 0)

    def test_no_stream_timeout_exceeded(self):
        """Test that timeout works with process_stream=False."""
        with tempfile.NamedTemporaryFile(suffix='.log', delete=False) as f:
            log_path = f.name

        # Command that sleeps longer than timeout
        cmd = ['sleep', '10']
        with self.assertRaises(subprocess.TimeoutExpired):
            log_lib.run_with_log(
                cmd,
                log_path,
                process_stream=False,
                timeout=1,
            )

    def test_no_stream_timeout_not_exceeded(self):
        """Test normal completion with process_stream=False and timeout set."""
        with tempfile.NamedTemporaryFile(suffix='.log', delete=False) as f:
            log_path = f.name

        # Command that completes quickly
        cmd = ['echo', 'hello']
        returncode = log_lib.run_with_log(
            cmd,
            log_path,
            process_stream=False,
            timeout=10,
        )
        self.assertEqual(returncode, 0)


if __name__ == '__main__':
    unittest.main()
