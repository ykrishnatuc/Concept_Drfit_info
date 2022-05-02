import numpy as np
from menelaus.drift_detector import DriftDetector


class EDDM(DriftDetector):
    """EDDM is a drift detection algorithm for binary classifiers which uses the
    distance between two classification errors. The running average distance
    between two errors (``dist_i``) and its standard deviation (``s_i``) are
    tracked for each element i in the data stream. The maximum values for these
    two estimates are stored and used to define the warning and drift
    thresholds. If the distance and its standard deviation exceed a certain
    threshold, relative to their maxima, we assume that the distance between
    errors is no longer stationary (drift has occurred).

    If ``(dist_i + 2 * s_i)/(dist_max + 2 * s_max) < warning_thresh``,
    the detector's state is set to ``"warning"``.

    If ``(dist_i + 2 * s_i)/(dist_max + 2 *s_max) < drift_thresh``,
    the detector's state is set to ``"drift"``.

    The denominator approximates the 95th percentile of the distribution of the
    distance for "large" samples.

    The index of the first sample which triggered a warning/drift state
    (relative to ``self.samples_since_reset``) is stored in ``self.retraining_recs``.

    Ref. M. Baena-Garcia, J. del Campo-avila, R. Fidalgo, a. Bifet, R. gavalda,
    and R. Morales-Bueno, "Early drift detection method," in Proc. 4th Int.
    Workshop Knowledge Discovery from Data Streams, 2006, Conference Paper.

    Attributes:
        total_samples (int): number of samples the drift detector has ever
            been updated with
        samples_since_reset (int): number of samples since the last time the
            drift detector was reset
        drift_state (str): detector's current drift state. Can take values
            ``"drift"``, ``"warning"``, or ``None``.
    """

    input_type = "stream"

    def __init__(self, n_threshold=30, warning_thresh=0.95, drift_thresh=0.9):
        """
        :param n_threshold: the minimum number of samples required to test
            whether drift has occurred
        :param warning_thresh: defines the threshold over which to enter the
            warning state.
        :param drift_thresh: defines the threshold over which to enter the
            drift state.
        """
        super().__init__()
        self.warning_thresh = warning_thresh
        self.drift_thresh = drift_thresh
        self.n_threshold = n_threshold
        self._n_errors = 0
        self._index_error_curr = 0
        self._index_error_last = 0
        self._dist_mean = 0
        self._dist_std = 0
        self._max_numerator = 0
        self._test_statistic = None
        self._initialize_retraining_recs()

    def reset(self):
        """Initialize the detector's drift state and other relevant attributes.
        Intended for use after ``drift_state == 'drift'``.
        """
        super().reset()
        self._n_errors = 0
        self._index_error_curr = 0
        self._index_error_last = 0
        self._dist_mean = 0
        self._dist_std = 0
        self._max_numerator = 0
        self._test_statistic = None
        self._initialize_retraining_recs()

    def update(self, y_pred, y_true):
        """Update the detector with a new sample.

        Args:
          y_pred: predicted class
          y_true: actual class
        """
        if self.drift_state == "drift":
            self.reset()

        super().update()
        classifier_result = int(y_pred == y_true)

        # found a new error, so update
        if not classifier_result:
            self._n_errors += 1
            # calculate the distance between two errors
            self._index_error_last = self._index_error_curr
            self._index_error_curr = (
                self.samples_since_reset - 1
            )  # n is count, not index!
            dist = self._index_error_curr - self._index_error_last

            # calculate an average (updated at each time step), of the distance
            #   between two errors: prior average distance + distance between
            #   the most recent two will be the new average
            prev_dist_mean = self._dist_mean
            self._dist_mean = (
                self._dist_mean + (dist - self._dist_mean) / self._n_errors
            )
            self._dist_std = self._dist_std + (dist - self._dist_mean) * (
                dist - prev_dist_mean
            )
            self._dist_std = np.sqrt(self._dist_std / self._n_errors)

            # it's unclear whether the 'burn-in' period should be updating the
            # maximums - seems like a bad idea though.
            if self._n_errors < self.n_threshold:
                return

            # check if the new average and SD are greater than the maximum, then
            # store what we need of them
            curr_numerator = self._dist_mean + 2 * self._dist_std
            if self._max_numerator < curr_numerator:
                self._max_numerator = curr_numerator

            # if the ratio of the current (p+2s)/(pmax + 2smax) is greater than
            # threshold, warn
            self._test_statistic = curr_numerator / self._max_numerator

            if self._test_statistic <= self.drift_thresh:
                self.drift_state = "drift"
            elif self._test_statistic <= self.warning_thresh:
                self.drift_state = "warning"
            else:
                self.drift_state = None

            if self.drift_state is not None:
                self._increment_retraining_recs()

    def _initialize_retraining_recs(self):
        """Sets ``self._retraining_recs`` to ``[None, None]``."""
        self._retraining_recs = [None, None]

    def _increment_retraining_recs(self):
        """Set ``self._retraining_recs`` to the beginning and end of the current
        drift/warning region.
        """
        if self.drift_state == "warning" and self._retraining_recs[0] is None:
            self._retraining_recs[0] = self.total_samples - 1

        if self.drift_state == "drift":
            self._retraining_recs[1] = self.total_samples - 1
            if self._retraining_recs[0] is None:
                self._retraining_recs[0] = self.total_samples - 1

    @property
    def retraining_recs(self):
        """Recommended indices for retraining. Usually ``[first warning index,
        drift index]``. If no warning state occurs, this will instead be
        ``[drift index, drift index]`` -- this indicates an abrupt change.
        Resets when ``self.drift_state`` returns to ``None`` (no drift nor warning).

        Returns:
            list: the current retraining recommendations
        """
        return self._retraining_recs
