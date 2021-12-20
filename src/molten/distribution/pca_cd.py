import statistics
import numpy as np
import pandas as pd
import math
from sklearn.decomposition import PCA
from sklearn.preprocessing import StandardScaler
from molten.drift_detector import DriftDetector
from molten.distribution.kl_divergence import kl_divergence
from molten.other.page_hinkley import PageHinkley


class PCACD(DriftDetector):
    """Principal Component Analysis Change Detection (PCA-CD) is a drift
    detection algorithm which checks for change in the distribution of the given
    data using one of several divergence metrics calculated on the data's
    principal components.

    First, principal components are built from the reference window - the
    initial window_size samples. New samples from the test window, of the same
    width, are projected onto these principal components. The divergence metric
    is calculated on these scores for the reference and test windows; if this
    metric diverges enough, then we consider drift to have occurred. This
    threshold is determined dynamically through the use of the Page-Hinkley test.

    Once drift is detected, the reference window is replaced with the current
    test window, and the test window is initialized.

    Ref. Qahtan, A., Wang, S. A PCA-Based Change Detection Framework for
    Multidimensional Data Streams Categories and Subject Descriptors. KDD '15:
    The 21st ACM SIGKDD International Conference on Knowledge Discovery and Data
    Mining, 935-44. https://doi.org/10.1145/2783258.2783359

    Attributes:
        total_samples (int): number of samples the drift detector has ever
            been updated with
        samples_since_reset (int): number of samples since the last time the
            drift detector was reset
        drift_state (str): detector's current drift state. Can take values
            "drift", "warning", or None.
        step (int): how frequently (by number of samples), to detect drift.
            This is either 100 samples or sample_period * window_size, whichever
            is smaller.
        ph_threshold (float): threshold parameter for the internal Page-Hinkley
            detector. Takes the value of .01 * window_size.
        num_pcs (int): the number of principal components being used to meet
            the specified ev_threshold parameter.
    """

    def __init__(
        self,
        window_size,
        ev_threshold=0.99,
        delta=0.1,
        density="kde",
        divergence_metric="kl",
        sample_period=0.05,
        online_scaling=False,
        track_state=False,
    ):
        """
        Args:
            window_size (int): size of the reference window. Note that PCA_CD
                will only try to detect drift periodically, either every 100
                observations or 5% of the window_size, whichever is smaller.
            ev_threshold (float, optional): Threshold for percent explained
                variance required when selecting number of principal components.
                Defaults to 0.99.
            delta (float, optional): Parameter for Page Hinkley test. Minimum
                amplitude of change in data needed to sound alarm. Defaults to 0.1.
            density (str, optional): density estimate when computing distributions
                of two windows. Defaults to "kde"
                    "kde" - use kernel density estimation with epanechnikov kernel
                    "histograms" - uses histograms to estimate densities of windows.
                    A discontinuous, less accurate density estimate that should
                    only be used when efficiency is of concern.
            divergence_metric (str, optional): divergence metric when comparing
                the two distributions when detecting drift. Defaults to "kl".
                    "kl" - symmetric Kullback-Leibler divergence
                    "llh" - log-likelihood
                    "intersection" - intersection area under the curves for the
                    estimated density functions.
            sample_period (float, optional): how often to check for drift. This
                is 100 samples or sample_period * window_size, whichever is
                smaller. Default .05, or 5% of the window size.
            online_scaling (bool, optional): whether to standardize the data as
                it comes in, using the reference window, before applying PCA.
                Defaults to False.
            track_state (bool, optional): whether to store the status of the
                Page Hinkley detector every time drift is identified.
                Defaults to False.
        """
        super().__init__()
        self.window_size = window_size
        self.ev_threshold = ev_threshold
        self.divergence_metric = divergence_metric
        self.density = density
        self.track_state = track_state
        self.sample_period = (
            sample_period  # TODO modify sample period dependent upon density estimate
        )

        # Initialize parameters
        self.step = min(100, round(self.sample_period * window_size))
        self.ph_threshold = round(0.01 * window_size)
        self.bins = math.floor(math.sqrt(self.window_size))

        self.delta = delta

        self._drift_detection_monitor = PageHinkley(
            delta=self.delta, threshold=self.ph_threshold, burn_in=0
        )
        if self.track_state:
            self._drift_tracker = pd.DataFrame()

        self.num_pcs = None

        self.online_scaling = online_scaling
        if self.online_scaling is True:
            self._reference_scaler = StandardScaler()

        self._build_reference_and_test = True
        self._reference_window = pd.DataFrame()
        self._test_window = pd.DataFrame()
        self._pca = None
        self._reference_pca_projection = pd.DataFrame()
        self._test_pca_projection = pd.DataFrame()
        self._density_reference = {}
        self._density_test = {}

    def update(self, next_obs, *args, **kwargs):  # pylint: disable=arguments-differ
        """Update the detector with a new observation.

        Args:
            next_obs: next observation, as a pandas Series
        """

        if self._build_reference_and_test:
            if self.drift_state is not None:
                self._reference_window = self._test_window.copy()
                if self.online_scaling is True:
                    # we'll need to refit the scaler. this occurs when both reference and test
                    # windows are full, so, inverse_transform first, here
                    self._reference_window = pd.DataFrame(
                        self._reference_scaler.inverse_transform(self._reference_window)
                    )
                self._test_window = pd.DataFrame()
                self.reset()
                self._drift_detection_monitor.reset()

            elif len(self._reference_window) < self.window_size:
                self._reference_window = self._reference_window.append(next_obs)

            elif len(self._test_window) < self.window_size:
                self._test_window = self._test_window.append(next_obs)

            if len(self._test_window) == self.window_size:
                self._build_reference_and_test = False

                # Fit Reference window onto PCs
                if self.online_scaling is True:
                    self._reference_window = pd.DataFrame(
                        self._reference_scaler.fit_transform(self._reference_window)
                    )
                    self._test_window = pd.DataFrame(
                        self._reference_scaler.transform(self._test_window)
                    )

                # Compute principal components
                self._pca = PCA(self.ev_threshold)
                self._pca.fit(self._reference_window)
                self.num_pcs = len(self._pca.components_)

                # Project Reference window onto PCs
                self._reference_pca_projection = pd.DataFrame(
                    self._pca.transform(self._reference_window),
                    # columns=[f"PC{i}" for i in list(range(1, self.num_pcs + 1))],
                    # index=self._reference_window.index,
                )

                # Compute reference distribution
                for i in range(self.num_pcs):

                    if self.density == "kde":
                        self._density_reference[f"PC{i + 1}"] = self._build_kde_track(
                            self._reference_pca_projection.iloc[:, i]
                        )

                    else:

                        self._density_reference[f"PC{i + 1}"] = self._build_histograms(
                            self._reference_pca_projection.iloc[:, i], bins=self.bins
                        )

                # Project test window onto PCs
                self._test_pca_projection = pd.DataFrame(
                    self._pca.transform(self._test_window),
                    # columns=[f"PC{i}" for i in list(range(1, self.num_pcs + 1))],
                    # index=self._test_window.index,
                )

        else:

            # Add new obs to test window
            if self.online_scaling is True:
                next_obs = pd.DataFrame(self._reference_scaler.transform(next_obs))
            self._test_window = self._test_window.iloc[1:, :].append(next_obs)

            # Project new observation onto PCs
            next_proj = pd.DataFrame(
                self._pca.transform(np.array(next_obs).reshape(1, -1)),
                # columns=[f"PC{i}" for i in list(range(1, self.num_pcs + 1))],
                # index=pd.Series(self._test_window.index[-1]),
            )

            # Add projection to test projection data
            self._test_pca_projection = self._test_pca_projection.iloc[1:, :].append(
                next_proj, ignore_index=True
            )

            # Compute change score
            if (self.total_samples % self.step) == 0 and self.total_samples != 0:

                # Compute test distribution
                self._density_test = {}
                for i in range(self.num_pcs):

                    if self.density == "kde":
                        self._density_test[f"PC{i + 1}"] = self._build_kde_track(
                            self._test_pca_projection.iloc[:, i]
                        )

                    else:
                        self._density_test[f"PC{i + 1}"] = self._build_histograms(
                            self._test_pca_projection.iloc[:, i], bins=self.bins
                        )

                # Compute current score
                change_scores = []
                if self.divergence_metric == "kl":

                    for i in range(self.num_pcs):
                        change_scores.append(
                            max(
                                kl_divergence(
                                    self._density_reference[f"PC{i + 1}"]["density"],
                                    self._density_test[f"PC{i + 1}"]["density"],
                                    d_type="discrete",
                                ),
                                kl_divergence(
                                    self._density_test[f"PC{i + 1}"]["density"],
                                    self._density_reference[f"PC{i + 1}"]["density"],
                                    d_type="discrete",
                                ),
                            )
                        )

                elif self.divergence_metric == "intersection":
                    for i in range(self.num_pcs):
                        change_scores.append(
                            self._intersection_area(
                                self._density_reference[f"PC{i + 1}"]["density"],
                                self._density_test[f"PC{i + 1}"]["density"],
                            )
                        )

                elif self.divergence_metric == "llh":
                    for i in range(self.num_pcs):
                        change_scores.append(
                            self._log_likelihood(
                                self._density_reference[f"PC{i + 1}"]["point"],
                                self._density_test[f"PC{i + 1}"]["point"],
                            )
                        )

                change_score = max(change_scores)

                self._drift_detection_monitor.update(
                    next_obs=change_score, obs_id=next_obs.index.values[0]
                )

                if self._drift_detection_monitor.drift_state is not None:
                    self._build_reference_and_test = True
                    self.drift_state = "drift"
                    if self.track_state:
                        self._drift_tracker = self._drift_tracker.append(
                            self._drift_detection_monitor.to_dataframe()
                        )

        super().update()

    @staticmethod
    def _epanechnikov_kernel(x_j):
        """Calculate the Epanechnikov kernel value for a given value x_j, for
        use in kernel density estimation.

        Args:
            x_j: single value

        Returns:
            Epanechnikov kernel value for x_j.

        """
        if abs(x_j) <= 1:
            return (3 / 4) * (1 - (x_j ** 2))
        else:
            return 0

    @classmethod
    def _log_likelihood(cls, values_p, values_q):
        """Computes Log-Likelihood similarity between two distributions

        Args:
            values_p (list): List of values from first distribution
            values_q (list): List of values from second distribution

        Returns:
          Log-likelihood similarity

        """
        sample_length = len(values_p)
        bandwidth = 1.06 * statistics.stdev(values_q) * (sample_length ** (-1 / 5))
        llh_q = sum(
            [
                np.log(
                    sum(
                        [
                            (1 / sample_length)
                            * cls._epanechnikov_kernel((y - x) / bandwidth)
                            for x in values_p
                        ]
                    )
                )
                for y in values_q
            ]
        )
        llh_p = sum(
            [
                np.log(
                    sum(
                        [
                            (1 / sample_length)
                            * cls._epanechnikov_kernel((y - x) / bandwidth)
                            for x in values_p
                        ]
                    )
                )
                for y in values_p
            ]
        )
        divergence = abs((llh_q / len(values_q)) - (llh_p / len(values_p)))

        return divergence

    @staticmethod
    def _intersection_area(values_p, values_q):
        """Computes Intersection Area similarity between two distributions

        Args:
            values_p (list): List of values from first distribution
            values_q (list): List of values from second distribution

        Returns:
            Intersection area

        """
        divergence = (1 / 2) * sum([abs(x - y) for x, y in zip(values_p, values_q)])

        return divergence

    @classmethod
    def _build_kde_track(cls, values):
        """Compute the Kernel Density Estimate Track for a given 1D data stream

        Args:
            values: 1D data in which we desire to estimate its density function

        Returns:
            Bandwidth and dictionary of resampling points

        """
        sample_length = len(values)
        bandwidth = 1.06 * statistics.stdev(values) * (sample_length ** (-1 / 5))
        density = [
            (1 / (sample_length * bandwidth))
            * sum([cls._epanechnikov_kernel((x - x_j) / bandwidth) for x_j in values])
            for x in values
        ]

        return {"point": values, "density": density}

    @staticmethod
    def _build_histograms(values, bins):
        """Compute the histogram density estimates for a given 1D data stream. Density estimates consist of the value of
        the pdf in each bin, normalized s.t. integral over the entire range is 1

                Args:
                    values: 1D data in which we desire to estimate its density function
                    bins: number of bins for estimating histograms. Equal to sqrt of cardinality of ref window

                Returns:
                    Bandwidth and dictionary of resampling points

        """

        density = np.histogram(values, bins=bins, density=True)
        return {"point": values, "density": list(density[0])}
