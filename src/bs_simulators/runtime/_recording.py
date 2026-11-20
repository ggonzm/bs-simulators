"""Capture requests and rolling sample batches outside the physics worker."""

from collections import deque
from dataclasses import dataclass, field
import json
from pathlib import Path
import threading


_BUFFER_CAPACITY = 1024


class _Recording:
    """Write one capture, owned exclusively by the recording worker."""

    def __init__(self, path, initial):
        self._time_origin = initial['t']
        self._wall_origin = initial['wall_elapsed']
        self._stream = Path(path).open('x', encoding='utf-8')
        try:
            self.write(initial)
            self.flush()
        except BaseException:
            self._stream.close()
            raise

    def write(self, snapshot) -> None:
        """Serialize a copied row with capture-relative time fields."""
        row = dict(snapshot)
        row['t'] -= self._time_origin
        row['applied_at_sim_time'] -= self._time_origin
        row['wall_elapsed'] -= self._wall_origin
        self._stream.write(json.dumps(row, allow_nan=False) + '\n')

    def flush(self) -> None:
        """Flush the initial row or a completed sample batch."""
        self._stream.flush()

    def close(self, final) -> None:
        """Append the final committed state and release the stream even on failure."""
        try:
            self.write(final)
        finally:
            self._stream.close()


@dataclass
class _CaptureRequest:
    """An ordered file operation with a completion event for its caller."""

    kind: str
    state: dict
    path: object
    sequence: int
    previous_demand: bool
    done: threading.Event = field(default_factory=threading.Event)
    error: Exception | None = None

    def wait(self) -> None:
        """Wait outside simulation locks and propagate the operation's error."""
        self.done.wait()
        if self.error is not None:
            raise self.error


class _RecordingWorker:
    """Write rolling batches while preserving every capture request in order.

    Samples use a bounded deque that overwrites its oldest pending sample.
    Requests use a separate deque. Both receive a common ordering number so
    the writer can merge them after swapping out a batch under a short lock.
    File operations and serialization happen entirely outside that lock.
    """

    def __init__(self):
        self._condition = threading.Condition(threading.Lock())
        self._samples = deque(maxlen=_BUFFER_CAPACITY)
        self._requests = deque()
        self._demand = False
        self._sequence = 0
        self._last_request = 0
        self._finishing = False
        self._failure = None
        self._status = 'idle'
        self._path = None
        self._active = None
        self._last = None
        self._terminal = None
        self._thread = threading.Thread(target=self._run, name='simulation-recording', daemon=False)
        self._thread.start()

    def status(self) -> dict:
        """Return detached recording status without waiting for disk operations."""
        with self._condition:
            return {'status': self._status, 'path': self._path,
                    'error': None if self._failure is None else str(self._failure)}

    @property
    def alive(self) -> bool:
        """Whether this worker is still processing or closing its files."""
        return self._thread.is_alive()

    @property
    def accepting(self) -> bool:
        """Whether new capture requests can still be enqueued."""
        with self._condition:
            return not self._finishing and self._failure is None

    def _fail(self, error) -> None:
        """Report an asynchronous capture failure independently of plant status."""
        with self._condition:
            if self._failure is None:
                self._failure = error
            self._status = 'error'
            self._demand = False
            self._samples.clear()
            self._condition.notify()

    def raise_error(self) -> None:
        """Propagate a stored writer failure to a capture or lifecycle caller."""
        with self._condition:
            error = self._failure
        if error is not None:
            raise error

    def request(self, kind, state, path=None) -> _CaptureRequest:
        """Preserve a file operation independently of sample-buffer overwrites."""
        with self._condition:
            if self._failure is not None:
                raise self._failure
            if self._finishing:
                raise RuntimeError('recording worker is closing')
            self._sequence += 1
            request = _CaptureRequest(kind, state, path, self._sequence, self._demand)
            self._requests.append(request)
            self._last_request = request.sequence
            self._demand = kind == 'start'
            self._condition.notify()
            return request

    def publish(self, state) -> None:
        """Append a sample in constant time, replacing the oldest when full."""
        with self._condition:
            if not self._demand or self._failure is not None or self._finishing:
                return
            self._sequence += 1
            self._samples.append((self._sequence, state))
            self._condition.notify()

    def finish(self, state) -> None:
        """Close ingress and drain queued work before writing the terminal state."""
        with self._condition:
            self._terminal = state
            self._finishing = True
            self._demand = False
            self._condition.notify()

    def wait(self, timeout=None) -> bool:
        """Wait for the writer, including its final file close."""
        self._thread.join(timeout)
        return not self._thread.is_alive()

    def _take_batch(self):
        """Swap pending buffers in constant time, releasing producers before I/O."""
        with self._condition:
            self._condition.wait_for(lambda: self._samples or self._requests
                                     or self._finishing or self._failure is not None)
            samples, requests = self._samples, self._requests
            self._samples = deque(maxlen=_BUFFER_CAPACITY)
            self._requests = deque()
            return samples, requests

    def _capture_request(self, request) -> None:
        """Apply one request on the writer, preserving the old file on an opening failure."""
        if request.kind == 'start':
            candidate = _Recording(request.path, request.state)
            previous = self._active
            self._active = candidate
            self._last = request.state
            with self._condition:
                self._path = str(request.path)
                self._status = 'recording'
            if previous is not None:
                previous.close(request.state)
        else:
            previous = self._active
            self._active = None
            try:
                if previous is not None:
                    previous.close(request.state)
            except Exception as error:
                self._fail(error)
                raise
            with self._condition:
                if self._failure is None:
                    self._status = 'idle'
                    self._path = None

    def _process_request(self, request) -> None:
        """Confirm a request after its file operation; retain errors for its caller."""
        try:
            self.raise_error()
            self._capture_request(request)
        except Exception as error:
            request.error = error
            with self._condition:
                if request.sequence == self._last_request:
                    self._demand = request.previous_demand and self._active is not None
        finally:
            request.done.set()

    def _write_batch(self, samples, requests) -> None:
        """Merge retained samples and lossless commands by their common order."""
        while samples or requests:
            if requests and (not samples or requests[0].sequence < samples[0][0]):
                self._process_request(requests.popleft())
            else:
                _, state = samples.popleft()
                with self._condition:
                    healthy = self._failure is None
                if healthy and self._active is not None:
                    try:
                        self._active.write(state)
                        self._last = state
                    except Exception as error:
                        self._fail(error)
        if self._active is not None:
            try:
                self._active.flush()
            except Exception as error:
                self._fail(error)

    def _run(self) -> None:
        """Drain batches and close the capture independently of physics execution."""
        try:
            while True:
                samples, requests = self._take_batch()
                if not samples and not requests:
                    break
                self._write_batch(samples, requests)
        finally:
            recording = self._active
            self._active = None
            if recording is not None:
                with self._condition:
                    failure = self._failure
                    final = dict(self._last if failure is not None else self._terminal)
                if failure is not None:
                    final['recording_error'] = f'{type(failure).__name__}: {failure}'
                try:
                    recording.close(final)
                except Exception as error:
                    self._fail(error)
            with self._condition:
                if self._failure is None:
                    self._status = 'idle'
                    self._path = None
