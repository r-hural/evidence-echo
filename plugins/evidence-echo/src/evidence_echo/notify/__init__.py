"""Report notifications: a short summary by Telegram and email, with the full report attached.

Lives beside the pipeline (the stages know nothing about it). A channel is a module with a
`send` function; a new channel is a new module plus one entry in commands.CHANNELS.
"""


class NotifyError(OSError):
    """A channel could not deliver (the text is safe to log: secrets are redacted)."""
