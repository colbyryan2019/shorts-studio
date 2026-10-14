class PipelineError(Exception):
    """A problem the user can fix (missing file, bad config, invalid script).

    make_episode.py prints the message and exits without a traceback.
    """
