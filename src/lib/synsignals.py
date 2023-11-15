import signal
import enum
import contextlib


_signals = None
_signums = None
_pending_signals = []
_handling_blocked = False


class BlockedHandling(contextlib.AbstractContextManager):
    def __enter__(self):
        block_handling()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        unblock_handling()
        return None


class PreserveHandler(enum.Enum):
    def _preserve_if_not_dfl(signum):
        # Non-default signal handlers can be established in various cases
        # (e.g. `nohup`, `env --ignore-signal=INT` commands).
        # It's usually done intentionally, so they should be preserved.
        match signum:
            # Python by default sets SIGINT handler
            # to `signal.default_int_handler`
            case signal.SIGINT:
                preserve = (signal.getsignal(signum) !=
                            signal.default_int_handler)
            # Python by default sets SIGPIPE handler
            # to `signal.SIG_IGN`
            case signal.SIGPIPE:
                preserve = signal.getsignal(signum) != signal.SIG_IGN
            # Other cases as usual.
            case _:
                preserve = signal.getsignal(signum) != signal.SIG_DFL

        return preserve

    def _preserve_always(signum):
        return True

    def _preserve_never(signum):
        return False

    AUTO = _preserve_if_not_dfl
    ALWAYS = _preserve_always
    NEVER = _preserve_never


def set(signals: dict, *, preserve_handler=PreserveHandler.AUTO):
    global _signals, _signums

    if not signals:
        block_handling()
        return

    if _signals is not None:
        raise ValueError("Signals can only be set once")

    signums = signals.keys()

    # Block signals before setting handler
    signal.pthread_sigmask(signal.SIG_BLOCK, signums)

    for s in signums:
        if preserve_handler(s):
            signals[s] = signal.getsignal(s)

    _signals = signals
    _signums = signums

    # Setting handlers
    for s in signums:
        signal.signal(s, _register_signal)

    # Now all handlers are set, so unblock signals
    signal.pthread_sigmask(signal.SIG_UNBLOCK, signums)


def add_handling(func):
    ''' Decorator that adds signal handling '''
    def decorator(*args, **kwargs):
        handle()
        return func(*args, **kwargs)
    return decorator


def handle():
    if _handling_blocked:
        return

    _block()
    try:
        while _pending_signals:
            signum, frame = _pending_signals.pop(0)

            handler = _signals[signum]

            if handler is not None and handler != signal.SIG_IGN:
                if handler != signal.SIG_DFL:
                    handler(signum, frame)
                else:
                    prev_handler = signal.signal(signum, signal.SIG_DFL)
                    signal.raise_signal(signum)
                    signal.pthread_sigmask(signal.SIG_UNBLOCK, [signum])

                    signal.pthread_sigmask(signal.SIG_BLOCK, [signum])
                    signal.signal(signum, prev_handler)

    finally:
        _unblock()


def get_pending_signals():
    _block()
    pending = _pending_signals.copy()
    _unblock()
    return pending


def block_handling():
    global _handling_blocked

    _handling_blocked = True


def unblock_handling():
    global _handling_blocked

    _handling_blocked = False
    # Handle signals right after unblocking
    handle()


def _register_signal(signum, frame):
    _block()
    _pending_signals.append((signum, frame))
    _unblock()


def _block():
    signal.pthread_sigmask(signal.SIG_BLOCK, _signums)


def _unblock():
    signal.pthread_sigmask(signal.SIG_UNBLOCK, _signums)
