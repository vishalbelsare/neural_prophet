import time
from collections import OrderedDict
import numpy as np
import pandas as pd

import torch
from torch.utils.data import DataLoader
import logging
from tqdm import tqdm

from neuralprophet import configure
from neuralprophet import time_net
from neuralprophet import time_dataset
from neuralprophet import df_utils
from neuralprophet import utils
from neuralprophet.plot_forecast import plot, plot_components
from neuralprophet.plot_model_parameters import plot_parameters
from neuralprophet import metrics

log = logging.getLogger("NP.forecaster")


METRICS = {
    "mae": metrics.MAE,
    "mse": metrics.MSE,
    "rmse": metrics.RMSE,
}


class NeuralProphet:
    """NeuralProphet forecaster.

    A simple yet powerful forecaster that models:
    Trend, seasonality, events, holidays, auto-regression, lagged covariates, and future-known regressors.
    Can be regualrized and configured to model nonlinear relationships.

    Parameters
    ----------
        COMMENT
        Trend Config
        COMMENT
        growth : str
            no trend or a linear trend ([``off`` or ``linear``])

            Note
            ----
            ``discontinuous`` setting is actually not a trend per se, only use if you know what you do.

        changepoints : list
            Dates at which to include potential changepoints.
            If not specified, potential changepoints are selected automatically.

            Note
            ----
            Data format: list of str, list of np.datetimes, np.array of np.datetimes (not np.array of np.str)

        n_changepoints : int
            Number of potential changepoints to include.

            Note
            ----
            Changepoints are selected uniformly from the first ``changepoint_range`` proportion of the history.
            Not used if input ``changepoints`` is supplied.
        changepoints_range : float
            Proportion of history in which trend changepoints will be estimated.

            Note
            ----
            Defaults to 0.8 for the first 80%. Not used if ``changepoints`` is specified.
        trend_reg : float
            Parameter modulating the flexibility of the automatic changepoint selection.

            Note
            ----
            Large values (~1-100) will limit the variability of changepoints.
            Small values (~0.001-1.0) will allow changepoints to change faster.
            default: 0 will fully fit a trend to each segment.

        trend_reg_threshold : bool
            Allowance for trend to change without regularization.

            Options
                * ``True``: Automatically set to a value that leads to a smooth trend.
                * (default) ``False``: All changes in changepoints are regularized

        COMMENT
        Seasonality Config
        COMMENT
        yearly_seasonality : bool, int
            Fit yearly seasonality.

            Options
                * ``True`` or ``False``
                * ``auto``: set automatically
                * ``value``: number of Fourier/linear terms to generate
        weekly_seasonality : bool, int
            Fit monthly seasonality.

            Options
                * ``True`` or ``False``
                * ``auto``: set automatically
                * ``value``: number of Fourier/linear terms to generate
        daily_seasonality : bool, int
            Fit daily seasonality.

            Options
                * ``True`` or ``False``
                * ``auto``: set automatically
                * ``value``: number of Fourier/linear terms to generate
        seasonality_mode : str
            Specifies mode of seasonality

            Options
                * (default) ``additive``
                * ``multiplicative``
        seasonality_reg : float
            Parameter modulating the strength of the seasonality model.

            Note
            ----
            Smaller values (~0.1-1) allow the model to fit larger seasonal fluctuations,
            larger values (~1-100) dampen the seasonality.
            default: None, no regularization

        COMMENT
        AR Config
        COMMENT
        n_lags : int
            Previous time series steps to include in auto-regression. Aka AR-order
        ar_reg : float
            how much sparsity to enduce in the AR-coefficients

            Note
            ----
            Large values (~1-100) will limit the number of nonzero coefficients dramatically.
            Small values (~0.001-1.0) will allow more non-zero coefficients.
            default: 0 no regularization of coefficients.

        COMMENT
        Model Config
        COMMENT
        n_forecasts : int
            Number of steps ahead of prediction time step to forecast.
        num_hidden_layers : int
            number of hidden layer to include in AR-Net (defaults to 0)
        d_hidden : int
            dimension of hidden layers of the AR-Net. Ignored if ``num_hidden_layers`` == 0.

        COMMENT
        Train Config
        COMMENT
        learning_rate : float
            Maximum learning rate setting for 1cycle policy scheduler.

            Note
            ----
            Default ``None``: Automatically sets the ``learning_rate`` based on a learning rate range test.
            For manual user input, (try values ~0.001-10).
        epochs : int
            Number of epochs (complete iterations over dataset) to train model.

            Note
            ----
            Default ``None``: Automatically sets the number of epochs based on dataset size.
            For best results also leave batch_size to None. For manual values, try ~5-500.
        batch_size : int
            Number of samples per mini-batch.

            Note
            ----
            Default ``None``: Automatically sets the batch_size based on dataset size.
            For best results also leave epochs to None. For manual values, try ~1-512.
        loss_func : str, torch.nn.functional.loss
            Type of loss to use:

            Options
                * ``Huber``: Huber loss function
                * ``MSE``: Mean Squared Error loss function
                * ``MAE``: Mean Absolute Error loss function
                * ``torch.nn.functional.loss.``: loss or callable for custom loss, eg. L1-Loss

            Examples
            --------
            >>> from neuralprophet import NeuralProphet
            >>> import torch
            >>> import torch.nn as nn
            >>> m = NeuralProphet(loss_func=torch.nn.L1Loss)

        collect_metrics : list, bool
            the names of metrics to compute. Valid: [``mae``, ``rmse``, ``mse``]

            Options
                * (default) ``True``: [``mae``, ``rmse``]
                * ``False``: No metrics

        COMMENT
        Missing Data
        COMMENT
        impute_missing : bool
            whether to automatically impute missing dates/values

            Note
            ----
            mputation follows a linear method up to 10 missing values, more are filled with trend.

        COMMENT
        Data Normalization
        COMMENT
        normalize : str
            Type of normalization to apply to the time series.

            Options
                * ``off`` bypasses data normalization
                * (default, binary timeseries) ``minmax`` scales the minimum value to 0.0 and the maximum value to 1.0
                * ``standardize`` zero-centers and divides by the standard deviation
                * (default) ``soft`` scales the minimum value to 0.0 and the 95th quantile to 1.0
                * ``soft1`` scales the minimum value to 0.1 and the 90th quantile to 0.9
        global_normalization : bool
            Activation of global normalization

            Options
                * ``True``: dict of dataframes is used as global_time_normalization
                * (default) ``False``: local normalization
        global_time_normalization (bool):
            Specifies global time normalization

            Options
                * (default) ``True``: only valid in case of global modeling local normalization
                * ``False``: set time data_params locally
        unknown_data_normalization : bool
            Specifies unknown data normalization

            Options
                * ``True``: test data is normalized with global data params even if trained with local data params (global modeling with local normalization)
                * (default) ``False``: no global modeling with local normalization
    """

    def __init__(
        self,
        growth="linear",
        changepoints=None,
        n_changepoints=10,
        changepoints_range=0.9,
        trend_reg=0,
        trend_reg_threshold=False,
        yearly_seasonality="auto",
        weekly_seasonality="auto",
        daily_seasonality="auto",
        seasonality_mode="additive",
        seasonality_reg=0,
        n_forecasts=1,
        n_lags=0,
        num_hidden_layers=0,
        d_hidden=None,
        ar_reg=None,
        learning_rate=None,
        epochs=None,
        batch_size=None,
        loss_func="Huber",
        optimizer="AdamW",
        newer_samples_weight=2,
        newer_samples_start=0.0,
        impute_missing=True,
        collect_metrics=True,
        normalize="auto",
        global_normalization=False,
        global_time_normalization=True,
        unknown_data_normalization=False,
    ):
        kwargs = locals()

        # General
        self.name = "NeuralProphet"
        self.n_forecasts = n_forecasts

        # Data Normalization settings
        self.config_normalization = configure.Normalization(
            normalize=normalize,
            global_normalization=global_normalization,
            global_time_normalization=global_time_normalization,
            unknown_data_normalization=unknown_data_normalization,
        )

        # Missing Data Preprocessing
        self.impute_missing = impute_missing
        self.impute_limit_linear = 5
        self.impute_rolling = 20

        # Training
        self.config_train = configure.from_kwargs(configure.Train, kwargs)

        if collect_metrics is None:
            collect_metrics = []
        elif collect_metrics is True:
            collect_metrics = ["mae", "rmse"]
        elif isinstance(collect_metrics, str):
            if not collect_metrics.lower() in METRICS.keys():
                raise ValueError("Received unsupported argument for collect_metrics.")
            collect_metrics = [collect_metrics]
        elif isinstance(collect_metrics, list):
            if not all([m.lower() in METRICS.keys() for m in collect_metrics]):
                raise ValueError("Received unsupported argument for collect_metrics.")
        elif collect_metrics is not False:
            raise ValueError("Received unsupported argument for collect_metrics.")

        self.metrics = None
        if isinstance(collect_metrics, list):
            self.metrics = metrics.MetricsCollection(
                metrics=[metrics.LossMetric(self.config_train.loss_func)]
                + [METRICS[m.lower()]() for m in collect_metrics],
                value_metrics=[metrics.ValueMetric("RegLoss")],
            )

        # AR
        self.config_ar = configure.from_kwargs(configure.AR, kwargs)
        self.n_lags = self.config_ar.n_lags
        if n_lags == 0 and n_forecasts > 1:
            self.n_forecasts = 1
            log.warning(
                "Changing n_forecasts to 1. Without lags, the forecast can be "
                "computed for any future time, independent of lagged values"
            )

        # Model
        self.config_model = configure.from_kwargs(configure.Model, kwargs)

        # Trend
        self.config_trend = configure.from_kwargs(configure.Trend, kwargs)

        # Seasonality
        self.season_config = configure.AllSeason(
            mode=seasonality_mode,
            reg_lambda=seasonality_reg,
            yearly_arg=yearly_seasonality,
            weekly_arg=weekly_seasonality,
            daily_arg=daily_seasonality,
        )
        self.config_train.reg_lambda_season = self.season_config.reg_lambda

        # Events
        self.events_config = None
        self.country_holidays_config = None

        # Extra Regressors
        self.config_covar = None
        self.regressors_config = None

        # set during fit()
        self.data_freq = None

        # Set during _train()
        self.fitted = False
        self.data_params = None
        self.optimizer = None
        self.scheduler = None
        self.model = None

        # set during prediction
        self.future_periods = None
        # later set by user (optional)
        self.highlight_forecast_step_n = None
        self.true_ar_weights = None

    def add_lagged_regressor(self, names, regularization=None, normalize="auto", only_last_value=False):
        """Add a covariate or list of covariate time series as additional lagged regressors to be used for fitting and predicting.
        The dataframe passed to ``fit`` and ``predict`` will have the column with the specified name to be used as
        lagged regressor. When normalize=True, the covariate will be normalized unless it is binary.

        Parameters
        ----------
            names : string or list
                name of the regressor/list of regressors.
            regularization : float
                optional  scale for regularization strength
            normalize : bool
                optional, specify whether this regressor will benormalized prior to fitting.
                if ``auto``, binary regressors will not be normalized.
            only_last_value : bool
                specifies last value handling

                Options
                    * (default) ``False`` use same number of lags as auto-regression
                    * ``True`` only use last known value as input
        """
        if self.fitted:
            raise Exception("Covariates must be added prior to model fitting.")
        if self.n_lags == 0:
            raise Exception("Covariates must be set jointly with Auto-Regression.")
        if not isinstance(names, list):
            names = [names]
        for name in names:
            self._validate_column_name(name)
            if self.config_covar is None:
                self.config_covar = OrderedDict({})
            self.config_covar[name] = configure.Covar(
                reg_lambda=regularization,
                normalize=normalize,
                as_scalar=only_last_value,
            )
        return self

    def add_future_regressor(self, name, regularization=None, normalize="auto", mode="additive"):
        """Add a regressor as lagged covariate with order 1 (scalar) or as known in advance (also scalar).
        The dataframe passed to :meth:`fit`  and :meth:`predict` will have a column with the specified name to be used as
        a regressor. When normalize=True, the regressor will be normalized unless it is binary.

        Parameters
        ----------
            name : string
                name of the regressor.
            regularization : float
                optional  scale for regularization strength
            normalize : bool
                optional, specify whether this regressor will be normalized prior to fitting.

                Note
                ----
                if ``auto``, binary regressors will not be normalized.
            mode : str
                ``additive`` (default) or ``multiplicative``.

        """
        if self.fitted:
            raise Exception("Regressors must be added prior to model fitting.")
        if regularization is not None:
            if regularization < 0:
                raise ValueError("regularization must be >= 0")
            if regularization == 0:
                regularization = None
        self._validate_column_name(name)

        if self.regressors_config is None:
            self.regressors_config = {}
        self.regressors_config[name] = configure.Regressor(reg_lambda=regularization, normalize=normalize, mode=mode)
        return self

    def add_events(self, events, lower_window=0, upper_window=0, regularization=None, mode="additive"):
        """
        Add user specified events and their corresponding lower, upper windows and the
        regularization parameters into the NeuralProphet object

        Parameters
        ----------
            events : str, list
                name or list of names of user specified events
            lower_window : int
                the lower window for the events in the list of events
            upper_window : int
                the upper window for the events in the list of events
            regularization : float
                optional  scale for regularization strength
            mode : str
                ``additive`` (default) or ``multiplicative``.

        """
        if self.fitted:
            raise Exception("Events must be added prior to model fitting.")

        if self.events_config is None:
            self.events_config = OrderedDict({})

        if regularization is not None:
            if regularization < 0:
                raise ValueError("regularization must be >= 0")
            if regularization == 0:
                regularization = None

        if not isinstance(events, list):
            events = [events]

        for event_name in events:
            self._validate_column_name(event_name)
            self.events_config[event_name] = configure.Event(
                lower_window=lower_window, upper_window=upper_window, reg_lambda=regularization, mode=mode
            )
        return self

    def add_country_holidays(self, country_name, lower_window=0, upper_window=0, regularization=None, mode="additive"):
        """
        Add a country into the NeuralProphet object to include country specific holidays
        and create the corresponding configs such as lower, upper windows and the regularization
        parameters

        Parameters
        ----------
            country_name : string
                name of the country
            lower_window : int
                the lower window for all the country holidays
            upper_window : int
                the upper window for all the country holidays
            regularization : float
                optional  scale for regularization strength
            mode : str
                ``additive`` (default) or ``multiplicative``.
        """
        if self.fitted:
            raise Exception("Country must be specified prior to model fitting.")

        if regularization is not None:
            if regularization < 0:
                raise ValueError("regularization must be >= 0")
            if regularization == 0:
                regularization = None
        self.country_holidays_config = configure.Holidays(
            country=country_name,
            lower_window=lower_window,
            upper_window=upper_window,
            reg_lambda=regularization,
            mode=mode,
        )
        self.country_holidays_config.init_holidays()
        return self

    def add_seasonality(self, name, period, fourier_order):
        """Add a seasonal component with specified period, number of Fourier components, and regularization.

        Increasing the number of Fourier components allows the seasonality to change more quickly
        (at risk of overfitting).
        Note: regularization and mode (additive/multiplicative) are set in the main init.

        Parameters
        ----------
            name : string
                name of the seasonality component.
            period : float
                number of days in one period.
            fourier_order : int
                number of Fourier components to use.

        """
        if self.fitted:
            raise Exception("Seasonality must be added prior to model fitting.")
        if name in ["daily", "weekly", "yearly"]:
            log.error("Please use inbuilt daily, weekly, or yearly seasonality or set another name.")
        # Do not Allow overwriting built-in seasonalities
        self._validate_column_name(name, seasons=True)
        if fourier_order <= 0:
            raise ValueError("Fourier Order must be > 0")
        self.season_config.append(name=name, period=period, resolution=fourier_order, arg="custom")
        return self

    def fit(self, df, freq="auto", validation_df=None, progress="bar", minimal=False):
        """Train, and potentially evaluate model.

        Parameters
        ----------
            df : pd.DataFrame, dict
                containing column ``ds``, ``y`` with all data
            freq : str
                Data step sizes. Frequency of data recording,

                Note
                ----
                Any valid frequency for pd.date_range, such as ``5min``, ``D``, ``MS`` or ``auto`` (default) to automatically set frequency.
            validation_df : pd.DataFrame, dict
                if provided, model with performance  will be evaluated after each training epoch over this data.
            epochs : int
                number of epochs to train (overrides default setting).
                default: if not specified, uses self.epochs
            progress : str
                Method of progress display

                Options
                    * (default) ``bar`` display updating progress bar (tqdm)
                    * ``print`` print out progress (fallback option)
                    * ``plot`` plot a live updating graph of the training loss, requires [live] install or livelossplot package installed.
                    * ``plot-all`` extended to all recorded metrics.
            minimal : bool
                whether to train without any printouts or metrics collection

        Returns
        -------
            pd.DataFrame
                metrics with training and potentially evaluation metrics
        """

        df_dict, _ = df_utils.prep_copy_df_dict(df)
        if self.fitted is True:
            log.error("Model has already been fitted. Re-fitting may break or produce different results.")
        df_dict = self._check_dataframe(df_dict, check_y=True, exogenous=True)
        self.data_freq = df_utils.infer_frequency(df_dict, n_lags=self.n_lags, freq=freq)
        df_dict = self._handle_missing_data(df_dict, freq=self.data_freq)
        if validation_df is not None and (self.metrics is None or minimal):
            log.warning("Ignoring validation_df because no metrics set or minimal training set.")
            validation_df = None
        if validation_df is None:
            if minimal:
                self._train_minimal(df_dict, progress_bar=progress == "bar")
                metrics_df = None
            else:
                metrics_df = self._train(df_dict, progress=progress)
        else:
            df_val_dict, _ = df_utils.prep_copy_df_dict(validation_df)
            df_val_dict = self._check_dataframe(df_val_dict, check_y=False, exogenous=False)
            df_val_dict = self._handle_missing_data(df_val_dict, freq=self.data_freq)
            metrics_df = self._train(df_dict, df_val_dict=df_val_dict, progress=progress)

        self.fitted = True
        return metrics_df

    def predict(self, df, decompose=True, raw=False):
        """Runs the model to make predictions.

        Expects all data needed to be present in dataframe.
        If you are predicting into the unknown future and need to add future regressors or events,
        please prepare data with make_future_dataframe.

        Parameters
        ----------
            df : pd.DataFrame, dict
                dataframe or dict of dataframes containing column ``ds``, ``y`` with data
            decompose : bool
                whether to add individual components of forecast to the dataframe
            raw : bool
                specifies raw data

                Options
                    * (default) ``False``: returns forecasts sorted by target (highlighting forecast age)
                    * ``True``: return the raw forecasts sorted by forecast start date

        Returns
        -------
            pd.DataFrame
                dependent on ``raw``

                Note
                ----

                ``raw == True``: columns ``ds``, ``y``, and [``step<i>``] where step<i> refers to the i-step-ahead
                prediction *made at* this row's datetime, e.g. step3 is the prediction for 3 steps into the future,
                predicted using information up to (excluding) this datetime.

                ``raw == False``: columns ``ds``, ``y``, ``trend`` and [``yhat<i>``] where yhat<i> refers to
                the i-step-ahead prediction for this row's datetime,
                e.g. yhat3 is the prediction for this datetime, predicted 3 steps ago, "3 steps old".
        """
        if raw:
            log.warning("Raw forecasts are incompatible with plotting utilities")
        if self.fitted is False:
            raise ValueError("Model has not been fitted. Predictions will be random.")
        df_dict, received_unnamed_df = df_utils.prep_copy_df_dict(df)
        # to get all forecasteable values with df given, maybe extend into future:
        df_dict, periods_added = self._maybe_extend_df(df_dict)
        df_dict = self._prepare_dataframe_to_predict(df_dict)
        # normalize
        df_dict = self._normalize(df_dict)
        for key, df_i in df_dict.items():
            dates, predicted, components = self._predict_raw(df_i, key, include_components=decompose)
            if raw:
                fcst = self._convert_raw_predictions_to_raw_df(dates, predicted, components)
                if periods_added[key] > 0:
                    fcst = fcst[:-1]
            else:
                fcst = self._reshape_raw_predictions_to_forecst_df(df_i, predicted, components)
                if periods_added[key] > 0:
                    fcst = fcst[: -periods_added[key]]
            df_dict[key] = fcst
        df = df_utils.maybe_get_single_df_from_df_dict(df_dict, received_unnamed_df)
        return df

    def test(self, df):
        """Evaluate model on holdout data.

        Parameters
        ----------
            df : pd.DataFrame,dict
                dataframe or dict of dataframes containing column ``ds``, ``y`` with with holdout data
        Returns
        -------
            pd.DataFrame
                evaluation metrics
        """
        df_dict, received_unnamed_df = df_utils.prep_copy_df_dict(df)
        if self.fitted is False:
            log.warning("Model has not been fitted. Test results will be random.")
        df_dict = self._check_dataframe(df_dict, check_y=True, exogenous=True)
        _ = df_utils.infer_frequency(df_dict, n_lags=self.n_lags, freq=self.data_freq)
        df_dict = self._handle_missing_data(df_dict, freq=self.data_freq)
        loader = self._init_val_loader(df_dict)
        val_metrics_df = self._evaluate(loader)
        if not self.config_normalization.global_normalization:
            log.warning("Note that the metrics are displayed in normalized scale because of local normalization.")
        return val_metrics_df

    def split_df(self, df, freq="auto", valid_p=0.2, local_split=False):
        """Splits timeseries df into train and validation sets.

        Prevents leakage of targets. Sharing/Overbleed of inputs can be configured.
        Also performs basic data checks and fills in missing data.

        Parameters
        ----------
            df : pd.DataFrame, dict
                dataframe or dict of dataframes containing column ``ds``, ``y`` with all data
            freq : str
                data step sizes. Frequency of data recording,

                Note
                ----
                Any valid frequency for pd.date_range, such as ``5min``, ``D``, ``MS`` or ``auto`` (default) to automatically set frequency.
            valid_p : float
                fraction of data to use for holdout validation set, targets will still never be shared.
            local_split : bool
                Each dataframe will be split according to valid_p locally (in case of dict of dataframes

        Returns
        -------
            tuple of two pd.DataFrames

                training data

                validation data

        See Also
        --------
            crossvalidation_split_df : Splits timeseries data in k folds for crossvalidation.
            double_crossvalidation_split_df : Splits timeseries data in two sets of k folds for crossvalidation on training and testing data.

        Examples
        --------
            >>> df1 = pd.DataFrame({'ds': pd.date_range(start='2022-12-01', periods=5,
            ...                     freq='D'), 'y': [9.59, 8.52, 8.18, 8.07, 7.89]})
            >>> df2 = pd.DataFrame({'ds': pd.date_range(start='2022-12-09', periods=5,
            ...                     freq='D'), 'y': [8.71, 8.09, 7.84, 7.65, 8.02]})
            >>> df3 = pd.DataFrame({'ds': pd.date_range(start='2022-12-09', periods=5,
            ...                     freq='D'), 'y': [7.67, 7.64, 7.55, 8.25, 8.3]})
            >>> df3
                ds	        y
            0	2022-12-09	7.67
            1	2022-12-10	7.64
            2	2022-12-11	7.55
            3	2022-12-12	8.25
            4	2022-12-13	8.30

        One can define a dict with many time series.
            >>> df_dict = {'data1': df1, 'data2': df2, 'data3': df3}

        You can split a single dataframe.
            >>> (df_train, df_val) = m.split_df(df3, valid_p=0.2)
            >>> df_train
                ds	        y
            0	2022-12-09	7.67
            1	2022-12-10	7.64
            2	2022-12-11	7.55
            3	2022-12-12	8.25
            >>> df_val
                ds	        y
            0	2022-12-13	8.3

        You can also use a dict of dataframes (especially useful for global modeling), which will account for the time range of the whole group of time series as default.
            >>> (df_dict_train, df_dict_val) = m.split_df(df_dict, valid_p=0.2)
            >>> df_dict_train
            {'data1':           ds     y
            0 2022-12-01  9.59
            1 2022-12-02  8.52
            2 2022-12-03  8.18
            3 2022-12-04  8.07
            4 2022-12-05  7.89,
            'data2':           ds     y
            0 2022-12-09  8.71
            1 2022-12-10  8.09
            2 2022-12-11  7.84,
            'data3':           ds     y
            0 2022-12-09  7.67
            1 2022-12-10  7.64
            2 2022-12-11  7.55}
            >>> df_dict_val
            {'data2':           ds     y
            0 2022-12-12  7.65
            1 2022-12-13  8.02,
            'data3':           ds     y
            0 2022-12-12  8.25
            1 2022-12-13  8.30}

        In some applications, splitting locally each time series may be helpful. In this case, one should set `local_split` to True.
            >>> (df_dict_train, df_dict_val) = m.split_df(df_dict, valid_p=0.2,
            ... local_split=True)
            >>> df_dict_train
            {'data1':           ds     y
            0 2022-12-01  9.59
            1 2022-12-02  8.52
            2 2022-12-03  8.18
            3 2022-12-04  8.07,
            'data2':           ds     y
            0 2022-12-09  8.71
            1 2022-12-10  8.09
            2 2022-12-11  7.84
            3 2022-12-12  7.65,
            'data3':           ds     y
            0 2022-12-09  7.67
            1 2022-12-10  7.64
            2 2022-12-11  7.55
            3 2022-12-12  8.25}
            >>> df_dict_val
            {'data1':           ds     y
            0 2022-12-05  7.89,
            'data2':           ds     y
            0 2022-12-13  8.02,
            'data3':           ds    y
            0 2022-12-13  8.3}
        """
        df, received_unnamed_df = df_utils.prep_copy_df_dict(df)
        df = self._check_dataframe(df, check_y=False, exogenous=False)
        freq = df_utils.infer_frequency(df, n_lags=self.n_lags, freq=freq)
        df = self._handle_missing_data(df, freq=freq, predicting=False)
        df_train, df_val = df_utils.split_df(
            df,
            n_lags=self.n_lags,
            n_forecasts=self.n_forecasts,
            valid_p=valid_p,
            inputs_overbleed=True,
            local_split=local_split,
        )
        df_train = df_utils.maybe_get_single_df_from_df_dict(df_train, received_unnamed_df)
        df_val = df_utils.maybe_get_single_df_from_df_dict(df_val, received_unnamed_df)
        return df_train, df_val

    def crossvalidation_split_df(self, df, freq="auto", k=5, fold_pct=0.1, fold_overlap_pct=0.5):
        """Splits timeseries data in k folds for crossvalidation.

        Parameters
        ----------
            df : pd.DataFrame, dict
                dataframe or dict of dataframes containing column ``ds``, ``y`` with all data
            freq : str
                data step sizes. Frequency of data recording,

                Note
                ----
                Any valid frequency for pd.date_range, such as ``5min``, ``D``, ``MS`` or ``auto`` (default) to automatically set frequency.
            k : int
                number of CV folds
            fold_pct : float
                percentage of overall samples to be in each fold
            fold_overlap_pct : float
                percentage of overlap between the validation folds.

        Returns
        -------
            list of k tuples [(df_train, df_val), ...]

                training data

                validation data
        """
        if isinstance(df, dict):
            raise NotImplementedError("Crossvalidation not implemented for multiple dataframes")
        df = df.copy(deep=True)
        df = self._check_dataframe(df, check_y=False, exogenous=False)
        freq = df_utils.infer_frequency(df, n_lags=self.n_lags, freq=freq)
        df = self._handle_missing_data(df, freq=freq, predicting=False)
        folds = df_utils.crossvalidation_split_df(
            df,
            n_lags=self.n_lags,
            n_forecasts=self.n_forecasts,
            k=k,
            fold_pct=fold_pct,
            fold_overlap_pct=fold_overlap_pct,
        )
        return folds

    def double_crossvalidation_split_df(self, df, freq="auto", k=5, valid_pct=0.10, test_pct=0.10):
        """Splits timeseries data in two sets of k folds for crossvalidation on training and testing data.

        Parameters
        ----------
            df : pd.DataFrame, dict
                dataframe or dict of dataframes containing column ``ds``, ``y`` with all data
            freq : str
                data step sizes. Frequency of data recording,

                Note
                ----
                Any valid frequency for pd.date_range, such as ``5min``, ``D``, ``MS`` or ``auto`` (default) to automatically set frequency.
            k : int
                number of CV folds
            valid_pct : float
                percentage of overall samples to be in validation
            test_pct : float
                percentage of overall samples to be in test

        Returns
        -------
            tuple of k tuples [(folds_val, folds_test), …]
                elements same as :meth:`crossvalidation_split_df` returns
        """
        if isinstance(df, dict):
            raise NotImplementedError("Double crossvalidation not implemented for multiple dataframes")
        df = df.copy(deep=True)
        df = self._check_dataframe(df, check_y=False, exogenous=False)
        freq = df_utils.infer_frequency(df, n_lags=self.n_lags, freq=freq)
        df = self._handle_missing_data(df, freq=freq, predicting=False)
        folds_val, folds_test = df_utils.double_crossvalidation_split_df(
            df,
            n_lags=self.n_lags,
            n_forecasts=self.n_forecasts,
            k=k,
            valid_pct=valid_pct,
            test_pct=test_pct,
        )

        return folds_val, folds_test

    def create_df_with_events(self, df, events_df):
        """
        Create a concatenated dataframe with the time series data along with the events data expanded.

        Parameters
        ----------
            df : pd.DataFrame, dict
                dataframe or dict of dataframes containing column ``ds``, ``y`` with all data
            events_df : dict, pd.DataFrame
                containing column ``ds`` and ``event``

        Returns
        -------
            dict, pd.DataFrame
                columns ``y``, ``ds`` and other user specified events
        """
        if self.events_config is None:
            raise Exception(
                "The events configs should be added to the NeuralProphet object (add_events fn)"
                "before creating the data with events features"
            )
        df_dict, received_unnamed_df = df_utils.prep_copy_df_dict(df)
        df_dict = self._check_dataframe(df_dict, check_y=True, exogenous=False)
        if isinstance(events_df, pd.DataFrame):
            events_df_i = events_df.copy(deep=True)
        for df_name, df_i in df_dict.items():
            if isinstance(events_df, dict):
                events_df_i = events_df[df_name].copy(deep=True)
            for name in events_df_i["event"].unique():
                assert name in self.events_config
            df_out = df_utils.convert_events_to_features(
                df_i,
                events_config=self.events_config,
                events_df=events_df_i,
            )
            df_dict[df_name] = df_out.reset_index(drop=True)
        df = df_utils.maybe_get_single_df_from_df_dict(df_dict, received_unnamed_df)
        return df

    def make_future_dataframe(self, df, events_df=None, regressors_df=None, periods=None, n_historic_predictions=False):
        df_dict, received_unnamed_df = df_utils.prep_copy_df_dict(df)
        df_dict_events, received_unnamed_events_df = df_utils.prep_copy_df_dict(events_df)
        df_dict_regressors, received_unnamed_regressors_df = df_utils.prep_copy_df_dict(regressors_df)
        if received_unnamed_events_df:
            df_dict_events = {key: df_dict_events["__df__"] for key in df_dict.keys()}
        elif df_dict_events is None:
            df_dict_events = {key: None for key in df_dict.keys()}
        else:
            df_utils.compare_dict_keys(df_dict, df_dict_events, "dataframes", "events")
        if received_unnamed_regressors_df:
            df_dict_regressors = {key: df_dict_regressors["__df__"] for key in df_dict.keys()}
        elif df_dict_regressors is None:
            df_dict_regressors = {key: None for key in df_dict.keys()}
        else:
            df_utils.compare_dict_keys(df_dict, df_dict_regressors, "dataframes", "regressors")

        df_future_dataframe = {}
        for key in df_dict.keys():
            df_future_dataframe[key] = self._make_future_dataframe(
                df=df_dict[key],
                events_df=df_dict_events[key],
                regressors_df=df_dict_regressors[key],
                periods=periods,
                n_historic_predictions=n_historic_predictions,
            )
        df_future = df_utils.maybe_get_single_df_from_df_dict(df_future_dataframe, received_unnamed_df)
        return df_future

    def predict_trend(self, df):
        """Predict only trend component of the model.

        Parameters
        ----------
            df : pd.DataFrame, dict
                dataframe or dict of dataframes containing column ``ds``, ``y`` with all data

        Returns
        -------
            pd.DataFrame, dict
                trend on prediction dates.
        """
        df_dict, received_unnamed_df = df_utils.prep_copy_df_dict(df)
        df_dict = self._check_dataframe(df_dict, check_y=False, exogenous=False)
        df_dict = self._normalize(df_dict)
        for df_name, df in df_dict.items():
            t = torch.from_numpy(np.expand_dims(df["t"].values, 1))
            trend = self.model.trend(t).squeeze().detach().numpy()
            data_params = self.config_normalization.get_data_params(df_name)
            trend = trend * data_params["y"].scale + data_params["y"].shift
            df_dict[df_name] = pd.DataFrame({"ds": df["ds"], "trend": trend})
        df = df_utils.maybe_get_single_df_from_df_dict(df_dict, received_unnamed_df)
        return df

    def predict_seasonal_components(self, df):
        """Predict seasonality components

        Parameters
        ----------
            df : pd.DataFrame, dict
                dataframe or dict of dataframes containing columns ``ds``, ``y`` with all data

        Returns
        -------
            pd.DataFrame, dict
                seasonal components with columns of name <seasonality component name>
        """
        df_dict, received_unnamed_df = df_utils.prep_copy_df_dict(df)
        df_dict = self._check_dataframe(df_dict, check_y=False, exogenous=False)
        df_dict = self._normalize(df_dict)
        for df_name, df in df_dict.items():
            dataset = time_dataset.TimeDataset(
                df,
                name=df_name,
                season_config=self.season_config,
                # n_lags=0,
                # n_forecasts=1,
                predict_mode=True,
            )
            loader = DataLoader(dataset, batch_size=min(4096, len(df)), shuffle=False, drop_last=False)
            predicted = {}
            for name in self.season_config.periods:
                predicted[name] = list()
            for inputs, _, _ in loader:
                for name in self.season_config.periods:
                    features = inputs["seasonalities"][name]
                    y_season = torch.squeeze(self.model.seasonality(features=features, name=name))
                    predicted[name].append(y_season.data.numpy())

            for name in self.season_config.periods:
                predicted[name] = np.concatenate(predicted[name])
                if self.season_config.mode == "additive":
                    data_params = self.config_normalization.get_data_params(df_name)
                    predicted[name] = predicted[name] * data_params["y"].scale
            df_dict[df_name] = pd.DataFrame({"ds": df["ds"], **predicted})
        df = df_utils.maybe_get_single_df_from_df_dict(df_dict, received_unnamed_df)
        return df

    def set_true_ar_for_eval(self, true_ar_weights):
        """Configures model to evaluate closeness of AR weights to true weights.

        Parameters
        ----------
            true_ar_weights : np.array
                true AR-parameters, if known.
        """
        self.true_ar_weights = true_ar_weights

    def highlight_nth_step_ahead_of_each_forecast(self, step_number=None):
        """Set which forecast step to focus on for metrics evaluation and plotting.

        Parameters
        ----------
            step_number : int
                i-th step ahead forecast to use for statistics and plotting.
        """
        if step_number is not None:
            assert step_number <= self.n_forecasts
        self.highlight_forecast_step_n = step_number
        return self

    def plot(self, fcst, ax=None, xlabel="ds", ylabel="y", figsize=(10, 6)):
        """Plot the NeuralProphet forecast, including history.

        Parameters
        ----------
            fcst : pd.DataFrame
                output of self.predict.
            ax : matplotlib axes
                optional, matplotlib axes on which to plot.
            xlabel : string
                label name on X-axis
            ylabel : string
                label name on Y-axis
            figsize : tuple
                width, height in inches. default: (10, 6)
        """
        if isinstance(fcst, dict):
            log.error("Receiced more than one DataFrame. Use a for loop for many dataframes.")
        if self.n_lags > 0:
            num_forecasts = sum(fcst["yhat1"].notna())
            if num_forecasts < self.n_forecasts:
                log.warning(
                    "Too few forecasts to plot a line per forecast step." "Plotting a line per forecast origin instead."
                )
                return self.plot_last_forecast(
                    fcst,
                    ax=ax,
                    xlabel=xlabel,
                    ylabel=ylabel,
                    figsize=figsize,
                    include_previous_forecasts=num_forecasts - 1,
                    plot_history_data=True,
                )
        return plot(
            fcst=fcst,
            ax=ax,
            xlabel=xlabel,
            ylabel=ylabel,
            figsize=figsize,
            highlight_forecast=self.highlight_forecast_step_n,
        )

    def plot_last_forecast(
        self,
        fcst,
        ax=None,
        xlabel="ds",
        ylabel="y",
        figsize=(10, 6),
        include_previous_forecasts=0,
        plot_history_data=None,
    ):
        """Plot the NeuralProphet forecast, including history.

        Parameters
        ----------
            fcst : pd.DataFrame
                output of self.predict.
            ax : matplotlib axes
                Optional, matplotlib axes on which to plot.
            xlabel : str
                label name on X-axis
            ylabel : str
                abel name on Y-axis
            figsize : tuple
                 width, height in inches. default: (10, 6)
            include_previous_forecasts : int
                number of previous forecasts to include in plot
            plot_history_data : bool
                specifies plot of historical data
        Returns
        -------
            matplotlib.axes.Axes
                plot of NeuralProphet forecasting
        """
        if self.n_lags == 0:
            raise ValueError("Use the standard plot function for models without lags.")
        if isinstance(fcst, dict):
            log.error("Receiced more than one DataFrame. Use a for loop for many dataframes.")
        if plot_history_data is None:
            fcst = fcst[-(include_previous_forecasts + self.n_forecasts + self.n_lags) :]
        elif plot_history_data is False:
            fcst = fcst[-(include_previous_forecasts + self.n_forecasts) :]
        elif plot_history_data is True:
            fcst = fcst
        fcst = utils.fcst_df_to_last_forecast(fcst, n_last=1 + include_previous_forecasts)
        return plot(
            fcst=fcst,
            ax=ax,
            xlabel=xlabel,
            ylabel=ylabel,
            figsize=figsize,
            highlight_forecast=self.highlight_forecast_step_n,
            line_per_origin=True,
        )

    def plot_components(self, fcst, figsize=None, residuals=False):
        """Plot the NeuralProphet forecast components.

        Parameters
        ----------
            fcst : pd.DataFrame
                output of self.predict
            figsize : tuple
                width, height in inches.

                Note
                ----
                None (default):  automatic (10, 3 * npanel)

        Returns
        -------
            matplotlib.axes.Axes
                plot of NeuralProphet components
        """
        if isinstance(fcst, dict):
            log.error("Receiced more than one DataFrame. Use a for loop for many dataframes.")
        return plot_components(
            m=self,
            fcst=fcst,
            figsize=figsize,
            forecast_in_focus=self.highlight_forecast_step_n,
            residuals=residuals,
        )

    def plot_parameters(self, weekly_start=0, yearly_start=0, figsize=None, df_name=None):
        """Plot the NeuralProphet forecast components.

        Parameters
        ----------
            weekly_start : int
                specifying the start day of the weekly seasonality plot.

                Note
                ----
                0 (default) starts the week on Sunday. 1 shifts by 1 day to Monday, and so on.
            yearly_start : int
                specifying the start day of the yearly seasonality plot.

                Note
                ----
                0 (default) starts the year on Jan 1. 1 shifts by 1 day to Jan 2, and so on.
            df_name : str
                name of dataframe to refer to data params from original keys of train dataframes (used for local normalization in global modeling)
            figsize : tuple
                width, height in inches.

                Note
                ----
                None (default):  automatic (10, 3 * npanel)

        Returns
        -------
            matplotlib.axes.Axes
                plot of NeuralProphet forecasting
        """
        return plot_parameters(
            m=self,
            forecast_in_focus=self.highlight_forecast_step_n,
            weekly_start=weekly_start,
            yearly_start=yearly_start,
            figsize=figsize,
            df_name=df_name,
        )

    def _init_model(self):
        """Build Pytorch model with configured hyperparamters.

        Returns
        -------
            TimeNet model
        """
        self.model = time_net.TimeNet(
            config_trend=self.config_trend,
            config_season=self.season_config,
            config_covar=self.config_covar,
            config_regressors=self.regressors_config,
            config_events=self.events_config,
            config_holidays=self.country_holidays_config,
            n_forecasts=self.n_forecasts,
            n_lags=self.n_lags,
            num_hidden_layers=self.config_model.num_hidden_layers,
            d_hidden=self.config_model.d_hidden,
        )
        log.debug(self.model)
        return self.model

    def _create_dataset(self, df_dict, predict_mode):
        """Construct dataset from dataframe.

        (Configured Hyperparameters can be overridden by explicitly supplying them.
        Useful to predict a single model component.)

        Parameters
        ----------
            df_dict : dict
                containing pd.DataFrames of original and normalized columns ``ds``, ``y``, ``t``, ``y_scaled``
            df : pd.DataFrame, dict
                dataframe or dict of dataframes containing column ``ds``, ``y`` and
                normalized columns normalized columns ``ds``, ``y``, ``t``, ``y_scaled``
            predict_mode : bool
                specifies predict mode

                Options
                    * ``False``: includes target values.
                    * ``True``: does not include targets but includes entire dataset as input

        Returns
        -------
            TimeDataset
        """
        return time_dataset.GlobalTimeDataset(
            df_dict,
            predict_mode=predict_mode,
            n_lags=self.n_lags,
            n_forecasts=self.n_forecasts,
            season_config=self.season_config,
            events_config=self.events_config,
            country_holidays_config=self.country_holidays_config,
            covar_config=self.config_covar,
            regressors_config=self.regressors_config,
        )

    def __handle_missing_data(self, df, freq, predicting):
        """Checks, auto-imputes and normalizes new data

        Parameters
        ----------
            df : pd.DataFrame, dict
                dataframe or dict of dataframes containing column ``ds``, ``y`` with all data
            freq : str
                data step sizes. Frequency of data recording,

                Note
                ----
                Any valid frequency for pd.date_range, such as ``5min``, ``D``, ``MS`` or ``auto`` (default) to automatically set frequency.
            predicting : bool
                when no lags, allow NA values in ``y`` of forecast series or ``y`` to miss completely

        Returns
        -------
            pd.DataFrame
                preprocessed dataframe
        """
        if self.n_lags == 0 and not predicting:
            # we can drop rows with NA in y
            sum_na = sum(df["y"].isna())
            if sum_na > 0:
                df = df[df["y"].notna()]
                log.info("dropped {} NAN row in 'y'".format(sum_na))

        # add missing dates for autoregression modelling
        if self.n_lags > 0:
            df, missing_dates = df_utils.add_missing_dates_nan(df, freq=freq)
            if missing_dates > 0:
                if self.impute_missing:
                    log.info("{} missing dates added.".format(missing_dates))
                else:
                    raise ValueError(
                        "{} missing dates found. Please preprocess data manually or set impute_missing to True.".format(
                            missing_dates
                        )
                    )

        if self.regressors_config is not None:
            # if future regressors, check that they are not nan at end, else drop
            # we ignore missing events, as those will be filled in with zeros.
            reg_nan_at_end = 0
            for col in self.regressors_config.keys():
                col_nan_at_end = 0
                while len(df) > col_nan_at_end and df[col].isnull().iloc[-(1 + col_nan_at_end)]:
                    col_nan_at_end += 1
                reg_nan_at_end = max(reg_nan_at_end, col_nan_at_end)
            if reg_nan_at_end > 0:
                # drop rows at end due to missing future regressors
                df = df[:-reg_nan_at_end]
                log.info("Dropped {} rows at end due to missing future regressor values.".format(reg_nan_at_end))

        df_end_to_append = None
        nan_at_end = 0
        while len(df) > nan_at_end and df["y"].isnull().iloc[-(1 + nan_at_end)]:
            nan_at_end += 1
        if nan_at_end > 0:
            if predicting:
                # allow nans at end - will re-add at end
                if self.n_forecasts > 1 and self.n_forecasts < nan_at_end:
                    # check that not more than n_forecasts nans, else drop surplus
                    df = df[: -(nan_at_end - self.n_forecasts)]
                    # correct new length:
                    nan_at_end = self.n_forecasts
                    log.info(
                        "Detected y to have more NaN values than n_forecast can predict. "
                        "Dropped {} rows at end.".format(nan_at_end - self.n_forecasts)
                    )
                df_end_to_append = df[-nan_at_end:]
                df = df[:-nan_at_end]
            else:
                # training - drop nans at end
                df = df[:-nan_at_end]
                log.info(
                    "Dropped {} consecutive nans at end. "
                    "Training data can only be imputed up to last observation.".format(nan_at_end)
                )

        # impute missing values
        data_columns = []
        if self.n_lags > 0:
            data_columns.append("y")
        if self.config_covar is not None:
            data_columns.extend(self.config_covar.keys())
        if self.regressors_config is not None:
            data_columns.extend(self.regressors_config.keys())
        if self.events_config is not None:
            data_columns.extend(self.events_config.keys())
        for column in data_columns:
            sum_na = sum(df[column].isnull())
            if sum_na > 0:
                if self.impute_missing:
                    # use 0 substitution for holidays and events missing values
                    if self.events_config is not None and column in self.events_config.keys():
                        df[column].fillna(0, inplace=True)
                        remaining_na = 0
                    else:
                        df.loc[:, column], remaining_na = df_utils.fill_linear_then_rolling_avg(
                            df[column],
                            limit_linear=self.impute_limit_linear,
                            rolling=self.impute_rolling,
                        )
                    log.info("{} NaN values in column {} were auto-imputed.".format(sum_na - remaining_na, column))
                    if remaining_na > 0:
                        raise ValueError(
                            "More than {} consecutive missing values encountered in column {}. "
                            "{} NA remain. Please preprocess data manually.".format(
                                2 * self.impute_limit_linear + self.impute_rolling, column, remaining_na
                            )
                        )
                else:  # fail because set to not impute missing
                    raise ValueError(
                        "Missing values found. " "Please preprocess data manually or set impute_missing to True."
                    )
        if df_end_to_append is not None:
            df = df.append(df_end_to_append)
        return df

    def _handle_missing_data(self, df, freq, predicting=False):
        """Checks, auto-imputes and normalizes new data

        Parameters
        ----------
            df : pd.DataFrame, dict
                dataframe or dict of dataframes containing column ``ds``, ``y`` with all data
            freq : str
                data step sizes. Frequency of data recording,

                Note
                ----
                Any valid frequency for pd.date_range, such as ``5min``, ``D``, ``MS`` or ``auto`` (default) to automatically set frequency.
            predicting (bool): when no lags, allow NA values in ``y`` of forecast series or ``y`` to miss completely

        Returns
        -------
            pre-processed df
        """
        df_is_dict = True
        if isinstance(df, pd.DataFrame):
            df_is_dict = False
            df = {"__df__": df}
        elif not isinstance(df, dict):
            raise ValueError("Please insert valid df type (i.e. pd.DataFrame, dict)")
        df_handled_missing_dict = {}
        for key in df:
            df_handled_missing_dict[key] = self.__handle_missing_data(df[key], freq, predicting)
        if not df_is_dict:
            df_handled_missing_dict = df_handled_missing_dict["__df__"]
        return df_handled_missing_dict

    def _check_dataframe(self, df, check_y=True, exogenous=True):
        """Performs basic data sanity checks and ordering

        Prepare dataframe for fitting or predicting.

        Parameters
        ----------
            df : pd.DataFrame, dict
                dataframe or dict of dataframes containing column ``ds``, ``y`` with all data
            check_y : bool
                if df must have series values

                Note
                ----
                set to True if training or predicting with autoregression
            exogenous : bool
                whether to check covariates, regressors and events column names

        Returns
        -------
            pd.DataFrame
                checked dataframe
        """
        df_is_dict = True
        if isinstance(df, pd.DataFrame):
            df_is_dict = False
            df = {"__df__": df}
        elif not isinstance(df, dict):
            raise ValueError("Please insert valid df type (i.e. pd.DataFrame, dict)")
        checked_df = {}
        for key, df_i in df.items():
            checked_df[key] = df_utils.check_single_dataframe(
                df=df_i,
                check_y=check_y,
                covariates=self.config_covar if exogenous else None,
                regressors=self.regressors_config if exogenous else None,
                events=self.events_config if exogenous else None,
            )
        if not df_is_dict:
            checked_df = checked_df["__df__"]
        return checked_df

    def _validate_column_name(self, name, events=True, seasons=True, regressors=True, covariates=True):
        """Validates the name of a seasonality, event, or regressor.

        Parameters
        ----------
            name : str
                name of seasonality, event or regressor
            events : bool
                check if name already used for event
            seasons : bool
                check if name already used for seasonality
            regressors : bool
                check if name already used for regressor
        """
        reserved_names = [
            "trend",
            "additive_terms",
            "daily",
            "weekly",
            "yearly",
            "events",
            "holidays",
            "zeros",
            "extra_regressors_additive",
            "yhat",
            "extra_regressors_multiplicative",
            "multiplicative_terms",
        ]
        rn_l = [n + "_lower" for n in reserved_names]
        rn_u = [n + "_upper" for n in reserved_names]
        reserved_names.extend(rn_l)
        reserved_names.extend(rn_u)
        reserved_names.extend(["ds", "y", "cap", "floor", "y_scaled", "cap_scaled"])
        if name in reserved_names:
            raise ValueError("Name {name!r} is reserved.".format(name=name))
        if events and self.events_config is not None:
            if name in self.events_config.keys():
                raise ValueError("Name {name!r} already used for an event.".format(name=name))
        if events and self.country_holidays_config is not None:
            if name in self.country_holidays_config.holiday_names:
                raise ValueError(
                    "Name {name!r} is a holiday name in {country_holidays}.".format(
                        name=name, country_holidays=self.country_holidays_config.country
                    )
                )
        if seasons and self.season_config is not None:
            if name in self.season_config.periods:
                raise ValueError("Name {name!r} already used for a seasonality.".format(name=name))
        if covariates and self.config_covar is not None:
            if name in self.config_covar:
                raise ValueError("Name {name!r} already used for an added covariate.".format(name=name))
        if regressors and self.regressors_config is not None:
            if name in self.regressors_config.keys():
                raise ValueError("Name {name!r} already used for an added regressor.".format(name=name))

    def _normalize(self, df_dict):
        """Apply data scales.

        Applies data scaling factors to df using data_params.

        Parameters
        ----------
            df : pd.DataFrame, dict
                dataframe or dict of dataframes containing column ``ds``, ``y`` with all data

        Returns
        -------
            df_dict: dict of pd.DataFrame, normalized
        """
        for df_name, df_i in df_dict.items():
            data_params = self.config_normalization.get_data_params(df_name)
            df_dict[df_name] = df_utils.normalize(df_i, data_params)
        return df_dict

    def _init_train_loader(self, df_dict):
        """Executes data preparation steps and initiates training procedure.

        Parameters
        ----------
            df : pd.DataFrame, dict
                dataframe or dict of dataframes containing column ``ds``, ``y`` with all data

        Returns
        -------
            torch DataLoader
        """
        if not isinstance(df_dict, dict):
            raise ValueError("df_dict must be a dict of pd.DataFrames.")
        # if not self.fitted:
        self.config_normalization.init_data_params(
            df_dict=df_dict,
            covariates_config=self.config_covar,
            regressor_config=self.regressors_config,
            events_config=self.events_config,
        )

        df_dict = self._normalize(df_dict)
        # if not self.fitted:
        if self.config_trend.changepoints is not None:
            # scale user-specified changepoint times
            self.config_trend.changepoints = self._normalize(
                {"__df__": pd.DataFrame({"ds": pd.Series(self.config_trend.changepoints)})}
            )["__df__"]["t"].values

        df_merged, _ = df_utils.join_dataframes(df_dict)
        df_merged = df_merged.sort_values("ds")
        df_merged.drop_duplicates(inplace=True, keep="first", subset=["ds"])

        self.season_config = utils.set_auto_seasonalities(df_merged, season_config=self.season_config)
        if self.country_holidays_config is not None:
            self.country_holidays_config.init_holidays(df_merged)

        dataset = self._create_dataset(df_dict, predict_mode=False)  # needs to be called after set_auto_seasonalities
        self.config_train.set_auto_batch_epoch(n_data=len(dataset))

        loader = DataLoader(dataset, batch_size=self.config_train.batch_size, shuffle=True)

        # if not self.fitted:
        self.model = self._init_model()  # needs to be called after set_auto_seasonalities

        if self.config_train.learning_rate is None:
            self.config_train.learning_rate = self.config_train.find_learning_rate(self.model, dataset)
            log.info("lr-range-test selected learning rate: {:.2E}".format(self.config_train.learning_rate))
        self.optimizer = self.config_train.get_optimizer(self.model.parameters())
        self.scheduler = self.config_train.get_scheduler(self.optimizer, steps_per_epoch=len(loader))
        return loader

    def _init_val_loader(self, df_dict):
        """Executes data preparation steps and initiates evaluation procedure.

        Parameters
        ----------
            df : pd.DataFrame, dict
                dataframe or dict of dataframes containing column ``ds``, ``y`` with all data

        Returns
        -------
            torch DataLoader
        """
        df_dict = self._normalize(df_dict)
        dataset = self._create_dataset(df_dict, predict_mode=False)
        loader = DataLoader(dataset, batch_size=min(1024, len(dataset)), shuffle=False, drop_last=False)
        return loader

    def _get_time_based_sample_weight(self, t):
        weight = torch.ones_like(t)
        if self.config_train.newer_samples_weight > 1.0:
            end_w = self.config_train.newer_samples_weight
            start_t = self.config_train.newer_samples_start
            time = (t.detach() - start_t) / (1.0 - start_t)
            time = torch.maximum(torch.zeros_like(time), time)
            time = torch.minimum(torch.ones_like(time), time)  # time = 0 to 1
            time = np.pi * (time - 1.0)  # time =  -pi to 0
            time = 0.5 * torch.cos(time) + 0.5  # time =  0 to 1
            # scales end to be end weight times bigger than start weight
            # with end weight being 1.0
            weight = (1.0 + time * (end_w - 1.0)) / end_w
        return weight

    def _train_epoch(self, e, loader):
        """Make one complete iteration over all samples in dataloader and update model after each batch.

        Parameters
        ----------
            e : int
                current epoch number
            loader : torch DataLoader
                Training Dataloader
        """
        self.model.train()
        for i, (inputs, targets, meta) in enumerate(loader):
            # Run forward calculation
            predicted = self.model.forward(inputs)
            # Compute loss. no reduction.
            loss = self.config_train.loss_func(predicted, targets)
            # Weigh newer samples more.
            loss = loss * self._get_time_based_sample_weight(t=inputs["time"])
            loss = loss.mean()
            # Regularize.
            loss, reg_loss = self._add_batch_regualarizations(loss, e, i / float(len(loader)))
            self.optimizer.zero_grad()
            loss.backward()
            self.optimizer.step()
            self.scheduler.step()
            if self.metrics is not None:
                self.metrics.update(
                    predicted=predicted.detach(), target=targets.detach(), values={"Loss": loss, "RegLoss": reg_loss}
                )
        if self.metrics is not None:
            return self.metrics.compute(save=True)
        else:
            return None

    def _add_batch_regualarizations(self, loss, e, iter_progress):
        """Add regulatization terms to loss, if applicable

        Parameters
        ----------
            loss : torch Tensor, scalar
                current batch loss
            e : int
                current epoch number
            iter_progress : float
                this epoch's progress of iterating over dataset [0, 1]

        Returns
        -------
            loss, reg_loss
        """
        delay_weight = self.config_train.get_reg_delay_weight(e, iter_progress)

        reg_loss = torch.zeros(1, dtype=torch.float, requires_grad=False)
        if delay_weight > 0:
            # Add regularization of AR weights - sparsify
            if self.model.n_lags > 0 and self.config_ar.reg_lambda is not None:
                reg_ar = self.config_ar.regularize(self.model.ar_weights)
                reg_ar = torch.sum(reg_ar).squeeze() / self.n_forecasts
                reg_loss += self.config_ar.reg_lambda * reg_ar

            # Regularize trend to be smoother/sparse
            l_trend = self.config_trend.trend_reg
            if self.config_trend.n_changepoints > 0 and l_trend is not None and l_trend > 0:
                reg_trend = utils.reg_func_trend(
                    weights=self.model.get_trend_deltas,
                    threshold=self.config_train.trend_reg_threshold,
                )
                reg_loss += l_trend * reg_trend

            # Regularize seasonality: sparsify fourier term coefficients
            l_season = self.config_train.reg_lambda_season
            if self.model.season_dims is not None and l_season is not None and l_season > 0:
                for name in self.model.season_params.keys():
                    reg_season = utils.reg_func_season(self.model.season_params[name])
                    reg_loss += l_season * reg_season

            # Regularize events: sparsify events features coefficients
            if self.events_config is not None or self.country_holidays_config is not None:
                reg_events_loss = utils.reg_func_events(self.events_config, self.country_holidays_config, self.model)
                reg_loss += reg_events_loss

            # Regularize regressors: sparsify regressor features coefficients
            if self.regressors_config is not None:
                reg_regressor_loss = utils.reg_func_regressors(self.regressors_config, self.model)
                reg_loss += reg_regressor_loss

        reg_loss = delay_weight * reg_loss
        loss = loss + reg_loss
        return loss, reg_loss

    def _evaluate_epoch(self, loader, val_metrics):
        """Evaluates model performance.

        Parameters
        ----------
            loader : torch DataLoader
                instantiated Validation Dataloader (with TimeDataset)
            val_metrics : MetricsCollection
                alidation metrics to be computed.

        Returns
        -------
            dict with evaluation metrics
        """
        with torch.no_grad():
            self.model.eval()
            for inputs, targets, meta in loader:
                predicted = self.model.forward(inputs)
                val_metrics.update(predicted=predicted.detach(), target=targets.detach())
            val_metrics = val_metrics.compute(save=True)
        return val_metrics

    def _train(self, df_dict, df_val_dict=None, progress="bar"):
        """Execute model training procedure for a configured number of epochs.

        Parameters
        ----------
            df_dict : pd.DataFrame, dict
                dataframe or dict of dataframes containing column ``ds``, ``y`` with all data
            df_val_dict : pd.DataFrame, dict
                dataframe or dict of dataframes containing column ``ds``, ``y`` with validation data
            progress : str
                Method of progress display.

                Options
                    * (default) ``bar`` display updating progress bar (tqdm)
                    * ``print`` print out progress (fallback option)
                    * ``plot`` plot a live updating graph of the training loss, requires [live] install or livelossplot package installed.
                    * ``plot-all`` "plot" extended to all recorded metrics.

        Returns
        -------
            pd.DataFrame
                metrics
        """
        # parse progress arg
        progress_bar = False
        progress_print = False
        plot_live_loss = False
        plot_live_all_metrics = False
        if progress.lower() == "bar":
            progress_bar = True
        elif progress.lower() == "print":
            progress_print = True
        elif progress.lower() == "plot":
            plot_live_loss = True
        elif progress.lower() in ["plot-all", "plotall", "plot all"]:
            plot_live_loss = True
            plot_live_all_metrics = True
        elif not progress.lower() == "none":
            raise ValueError("received unexpected value for progress {}".format(progress))

        if self.metrics is None:
            log.info("No progress prints or plots possible because metrics are deactivated.")
            if df_val_dict is not None:
                log.warning("Ignoring supplied df_val as no metrics are specified.")
            if plot_live_loss or plot_live_all_metrics:
                log.warning("Can not plot live loss as no metrics are specified.")
                progress_bar = True
            if progress_print:
                log.warning("Can not print progress as no metrics are specified.")
            return self._train_minimal(df_dict, progress_bar=progress_bar)

        # set up data loader
        loader = self._init_train_loader(df_dict)
        # set up Metrics
        if self.highlight_forecast_step_n is not None:
            self.metrics.add_specific_target(target_pos=self.highlight_forecast_step_n - 1)
        if not self.config_normalization.global_normalization:
            log.warning("When Global modeling with local normalization, metrics are displayed in normalized scale.")
        else:
            if not self.config_normalization.normalize == "off":
                self.metrics.set_shift_scale(
                    (
                        self.config_normalization.global_data_params["y"].shift,
                        self.config_normalization.global_data_params["y"].scale,
                    )
                )

        validate = df_val_dict is not None
        if validate:
            val_loader = self._init_val_loader(df_val_dict)
            val_metrics = metrics.MetricsCollection([m.new() for m in self.metrics.batch_metrics])

        # set up printing and plotting
        if plot_live_loss:
            try:
                from livelossplot import PlotLosses

                live_out = ["MatplotlibPlot"]
                if not progress_bar:
                    live_out.append("ExtremaPrinter")
                live_loss = PlotLosses(outputs=live_out)
                plot_live_loss = True
            except:
                log.warning(
                    "To plot live loss, please install neuralprophet[live]."
                    "Using pip: 'pip install neuralprophet[live]'"
                    "Or install the missing package manually: 'pip install livelossplot'",
                    exc_info=True,
                )
                plot_live_loss = False
                progress_bar = True
        if progress_bar:
            training_loop = tqdm(
                range(self.config_train.epochs),
                total=self.config_train.epochs,
                leave=log.getEffectiveLevel() <= 20,
            )
        else:
            training_loop = range(self.config_train.epochs)

        start = time.time()
        # run training loop
        for e in training_loop:
            metrics_live = OrderedDict({})
            self.metrics.reset()
            if validate:
                val_metrics.reset()
            # run epoch
            epoch_metrics = self._train_epoch(e, loader)
            # collect metrics
            if validate:
                val_epoch_metrics = self._evaluate_epoch(val_loader, val_metrics)
                print_val_epoch_metrics = {k + "_val": v for k, v in val_epoch_metrics.items()}
            else:
                val_epoch_metrics = None
                print_val_epoch_metrics = OrderedDict({})
            # print metrics
            if progress_bar:
                training_loop.set_description(f"Epoch[{(e+1)}/{self.config_train.epochs}]")
                training_loop.set_postfix(ordered_dict=epoch_metrics, **print_val_epoch_metrics)
            elif progress_print:
                metrics_string = utils.print_epoch_metrics(epoch_metrics, e=e, val_metrics=val_epoch_metrics)
                if e == 0:
                    log.info(metrics_string.splitlines()[0])
                    log.info(metrics_string.splitlines()[1])
                else:
                    log.info(metrics_string.splitlines()[1])
            # plot metrics
            if plot_live_loss:
                metrics_train = list(epoch_metrics)
                metrics_live["log-{}".format(metrics_train[0])] = np.log(epoch_metrics[metrics_train[0]])
                if plot_live_all_metrics and len(metrics_train) > 1:
                    for i in range(1, len(metrics_train)):
                        metrics_live["{}".format(metrics_train[i])] = epoch_metrics[metrics_train[i]]
                if validate:
                    metrics_val = list(val_epoch_metrics)
                    metrics_live["val_log-{}".format(metrics_val[0])] = np.log(val_epoch_metrics[metrics_val[0]])
                    if plot_live_all_metrics and len(metrics_val) > 1:
                        for i in range(1, len(metrics_val)):
                            metrics_live["val_{}".format(metrics_val[i])] = val_epoch_metrics[metrics_val[i]]
                live_loss.update(metrics_live)
                if e % (1 + self.config_train.epochs // 20) == 0 or e + 1 == self.config_train.epochs:
                    live_loss.send()

        # return metrics as df
        log.debug("Train Time: {:8.3f}".format(time.time() - start))
        log.debug("Total Batches: {}".format(self.metrics.total_updates))
        metrics_df = self.metrics.get_stored_as_df()
        if validate:
            metrics_df_val = val_metrics.get_stored_as_df()
            for col in metrics_df_val.columns:
                metrics_df["{}_val".format(col)] = metrics_df_val[col]
        return metrics_df

    def _train_minimal(self, df_dict, progress_bar=False):
        """Execute minimal model training procedure for a configured number of epochs.

        Parameters
        ----------
            df_dict : pd.DataFrame, dict
                dataframe or dict of dataframes containing column ``ds``, ``y`` with all data

        Returns
        -------
            None
        """
        loader = self._init_train_loader(df_dict)
        if progress_bar:
            training_loop = tqdm(
                range(self.config_train.epochs),
                total=self.config_train.epochs,
                leave=log.getEffectiveLevel() <= 20,
            )
        else:
            training_loop = range(self.config_train.epochs)
        for e in training_loop:
            if progress_bar:
                training_loop.set_description(f"Epoch[{(e+1)}/{self.config_train.epochs}]")
            _ = self._train_epoch(e, loader)

    def _eval_true_ar(self):
        assert self.n_lags > 0
        if self.highlight_forecast_step_n is None:
            if self.n_lags > 1:
                raise ValueError("Please define forecast_lag for sTPE computation")
            forecast_pos = 1
        else:
            forecast_pos = self.highlight_forecast_step_n
        weights = self.model.ar_weights.detach().numpy()
        weights = weights[forecast_pos - 1, :][::-1]
        sTPE = utils.symmetric_total_percentage_error(self.true_ar_weights, weights)
        log.info("AR parameters: ", self.true_ar_weights, "\n", "Model weights: ", weights)
        return sTPE

    def _evaluate(self, loader):
        """Evaluates model performance.

        Parameters
        ----------
            loader : torch DataLoader
                instantiated Validation Dataloader (with TimeDataset)

        Returns
        -------
            pd.DataFrame
                evaluation metrics
        """
        val_metrics = metrics.MetricsCollection([m.new() for m in self.metrics.batch_metrics])
        if self.highlight_forecast_step_n is not None:
            val_metrics.add_specific_target(target_pos=self.highlight_forecast_step_n - 1)
        ## Run
        val_metrics_dict = self._evaluate_epoch(loader, val_metrics)

        if self.true_ar_weights is not None:
            val_metrics_dict["sTPE"] = self._eval_true_ar()
        log.info("Validation metrics: {}".format(utils.print_epoch_metrics(val_metrics_dict)))
        val_metrics_df = val_metrics.get_stored_as_df()
        return val_metrics_df

    def _make_future_dataframe(self, df, events_df, regressors_df, periods, n_historic_predictions):
        if periods == 0 and n_historic_predictions is True:
            log.warning(
                "Not extending df into future as no periods specified." "You can call predict directly instead."
            )
        df = df.copy(deep=True)
        _ = df_utils.infer_frequency(df, n_lags=self.n_lags, freq=self.data_freq)
        last_date = pd.to_datetime(df["ds"].copy(deep=True).dropna()).sort_values().max()
        if events_df is not None:
            events_df = events_df.copy(deep=True).reset_index(drop=True)
        if regressors_df is not None:
            regressors_df = regressors_df.copy(deep=True).reset_index(drop=True)
        n_lags = 0 if self.n_lags is None else self.n_lags
        if periods is None:
            periods = 1 if n_lags == 0 else self.n_forecasts
        else:
            assert periods >= 0

        if isinstance(n_historic_predictions, bool):
            if n_historic_predictions:
                n_historic_predictions = len(df) - n_lags
            else:
                n_historic_predictions = 0
        elif not isinstance(n_historic_predictions, int):
            log.error("non-integer value for n_historic_predictions set to zero.")
            n_historic_predictions = 0

        if periods == 0 and n_historic_predictions == 0:
            raise ValueError("Set either history or future to contain more than zero values.")

        # check for external regressors known in future
        if self.regressors_config is not None and periods > 0:
            if regressors_df is None:
                raise ValueError("Future values of all user specified regressors not provided")
            else:
                for regressor in self.regressors_config.keys():
                    if regressor not in regressors_df.columns:
                        raise ValueError("Future values of user specified regressor {} not provided".format(regressor))

        if len(df) < n_lags:
            raise ValueError("Insufficient data for a prediction")
        elif len(df) < n_lags + n_historic_predictions:
            log.warning(
                "Insufficient data for {} historic forecasts, reduced to {}.".format(
                    n_historic_predictions, len(df) - n_lags
                )
            )
            n_historic_predictions = len(df) - n_lags
        if (n_historic_predictions + n_lags) == 0:
            df = pd.DataFrame(columns=df.columns)
        else:
            df = df[-(n_lags + n_historic_predictions) :]

        if len(df) > 0:
            if len(df.columns) == 1 and "ds" in df:
                assert n_lags == 0
                df = self._check_dataframe(df, check_y=False, exogenous=False)
            else:
                df = self._check_dataframe(df, check_y=n_lags > 0, exogenous=True)

        # future data
        # check for external events known in future
        if self.events_config is not None and periods > 0 and events_df is None:
            log.warning(
                "Future values not supplied for user specified events. "
                "All events being treated as not occurring in future"
            )

        if n_lags > 0:
            if periods > 0 and periods != self.n_forecasts:
                periods = self.n_forecasts
                log.warning(
                    "Number of forecast steps is defined by n_forecasts. " "Adjusted to {}.".format(self.n_forecasts)
                )

        if periods > 0:
            future_df = df_utils.make_future_df(
                df_columns=df.columns,
                last_date=last_date,
                periods=periods,
                freq=self.data_freq,
                events_config=self.events_config,
                events_df=events_df,
                regressor_config=self.regressors_config,
                regressors_df=regressors_df,
            )
            if len(df) > 0:
                df = df.append(future_df)
            else:
                df = future_df
        df.reset_index(drop=True, inplace=True)
        return df

    def _get_maybe_extend_periods(self, df):
        n_lags = 0 if self.n_lags is None else self.n_lags
        periods_add = 0
        nan_at_end = 0
        while len(df) > nan_at_end and df["y"].isnull().iloc[-(1 + nan_at_end)]:
            nan_at_end += 1
        if n_lags > 0:
            if self.regressors_config is None:
                # if dataframe has already been extended into future,
                # don't extend beyond n_forecasts.
                periods_add = max(0, self.n_forecasts - nan_at_end)
            else:
                # can not extend as we lack future regressor values.
                periods_add = 0
        return periods_add

    def _maybe_extend_df(self, df_dict):
        periods_add = {}
        for df_name, df in df_dict.items():
            _ = df_utils.infer_frequency(df, n_lags=self.n_lags, freq=self.data_freq)
            # to get all forecasteable values with df given, maybe extend into future:
            periods_add[df_name] = self._get_maybe_extend_periods(df)
            if periods_add[df_name] > 0:
                # This does not include future regressors or events.
                # periods should be 0 if those are configured.
                last_date = pd.to_datetime(df["ds"].copy(deep=True)).sort_values().max()
                future_df = df_utils.make_future_df(
                    df_columns=df.columns,
                    last_date=last_date,
                    periods=periods_add[df_name],
                    freq=self.data_freq,
                )
                df = df.append(future_df)
                df.reset_index(drop=True, inplace=True)
            df_dict[df_name] = df
        return df_dict, periods_add

    def _prepare_dataframe_to_predict(self, df_dict):
        for df_name, df in df_dict.items():
            df = df.copy(deep=True)
            _ = df_utils.infer_frequency(df, n_lags=self.n_lags, freq=self.data_freq)
            # check if received pre-processed df
            if "y_scaled" in df.columns or "t" in df.columns:
                raise ValueError(
                    "DataFrame has already been normalized. " "Please provide raw dataframe or future dataframe."
                )
            # Checks
            n_lags = 0 if self.n_lags is None else self.n_lags
            if len(df) == 0 or len(df) < n_lags:
                raise ValueError("Insufficient data to make predictions.")
            if len(df.columns) == 1 and "ds" in df:
                if n_lags != 0:
                    raise ValueError("only datestamps provided but y values needed for auto-regression.")
                df = self._check_dataframe(df, check_y=False, exogenous=False)
            else:
                df = self._check_dataframe(df, check_y=n_lags > 0, exogenous=False)
                # fill in missing nans except for nans at end
                df = self._handle_missing_data(df, freq=self.data_freq, predicting=True)
            df.reset_index(drop=True, inplace=True)
            df_dict[df_name] = df
        return df_dict

    def _predict_raw(self, df, df_name, include_components=False):
        """Runs the model to make predictions.

        Predictions are returned in raw vector format without decomposition.
        Predictions are given on a forecast origin basis, not on a target basis.

        Parameters
        ----------
            df : pd.DataFrame, dict
                dataframe or dict of dataframes containing column ``ds``, ``y`` with all data
            df_name : str
                name of the data params from which the current dataframe refers to (only in case of local_normalization)
            include_components : bool
                whether to return individual components of forecast

        Returns
        -------
            pd.Series
                timestamps referring to the start of the predictions.
            np.array
                array containing the forecasts
            dict[np.array]
                Dictionary of components containing an array of each components contribution to the forecast
        """
        if isinstance(df, dict):
            raise ValueError("Receiced more than one DataFrame. Use a for loop for many dataframes.")
        if "y_scaled" not in df.columns or "t" not in df.columns:
            raise ValueError("Received unprepared dataframe to predict. " "Please call predict_dataframe_to_predict.")
        dataset = self._create_dataset(df_dict={df_name: df}, predict_mode=True)
        loader = DataLoader(dataset, batch_size=min(1024, len(df)), shuffle=False, drop_last=False)
        if self.n_forecasts > 1:
            dates = df["ds"].iloc[self.n_lags : -self.n_forecasts + 1]
        else:
            dates = df["ds"].iloc[self.n_lags :]
        predicted_vectors = list()
        component_vectors = None

        with torch.no_grad():
            self.model.eval()
            for inputs, _, _ in loader:
                predicted = self.model.forward(inputs)
                predicted_vectors.append(predicted.detach().numpy())

                if include_components:
                    components = self.model.compute_components(inputs)
                    if component_vectors is None:
                        component_vectors = {name: [value.detach().numpy()] for name, value in components.items()}
                    else:
                        for name, value in components.items():
                            component_vectors[name].append(value.detach().numpy())

        predicted = np.concatenate(predicted_vectors)
        data_params = self.config_normalization.get_data_params(df_name)
        scale_y, shift_y = data_params["y"].scale, data_params["y"].shift
        predicted = predicted * scale_y + shift_y

        if include_components:
            components = {name: np.concatenate(value) for name, value in component_vectors.items()}
            for name, value in components.items():
                if "multiplicative" in name:
                    continue
                elif "event_" in name:
                    event_name = name.split("_")[1]
                    if self.events_config is not None and event_name in self.events_config:
                        if self.events_config[event_name].mode == "multiplicative":
                            continue
                    elif (
                        self.country_holidays_config is not None
                        and event_name in self.country_holidays_config.holiday_names
                    ):
                        if self.country_holidays_config.mode == "multiplicative":
                            continue
                elif "season" in name and self.season_config.mode == "multiplicative":
                    continue

                # scale additive components
                components[name] = value * scale_y
                if "trend" in name:
                    components[name] += shift_y
        else:
            components = None
        return dates, predicted, components

    def _convert_raw_predictions_to_raw_df(self, dates, predicted, components=None):
        """Turns forecast-origin-wise predictions into forecast-target-wise predictions.

        Parameters
        ----------
            dates : pd.Series
                timestamps referring to the start of the predictions.
            predicted : np.array
                Array containing the forecasts
            components : dict[np.array]
                Dictionary of components containing an array of each components' contribution to the forecast

        Returns
        -------
            pd. DataFrame
                columns ``ds``, ``y``, and [``step<i>``]

                Note
                ----
                where step<i> refers to the i-step-ahead prediction *made at* this row's datetime.
                e.g. the first forecast step0 is the prediction for this timestamp,
                the step1 is for the timestamp after, ...
                ... step3 is the prediction for 3 steps into the future,
                predicted using information up to (excluding) this datetime.
        """
        if isinstance(dates, dict):
            raise ValueError("Receiced more than one DataFrame. Use a for loop for many dataframes.")
        predicted_names = ["step{}".format(i) for i in range(self.n_forecasts)]
        all_data = predicted
        all_names = predicted_names
        if components is not None:
            for comp_name, comp_data in components.items():
                all_data = np.concatenate((all_data, comp_data), 1)
                all_names += ["{}{}".format(comp_name, i) for i in range(self.n_forecasts)]

        df_raw = pd.DataFrame(data=all_data, columns=all_names)
        df_raw.insert(0, "ds", dates.values)
        return df_raw

    def _reshape_raw_predictions_to_forecst_df(self, df, predicted, components):  # DOES NOT ACCEPT DICT
        """Turns forecast-origin-wise predictions into forecast-target-wise predictions.

        Parameters
        ----------
            df : pd.DataFrame
                input dataframe
            predicted : np.array
                Array containing the forecasts
            components : dict[np.array]
                Dictionary of components containing an array of each components' contribution to the forecast

        Returns
        -------
            pd.DataFrame
                columns ``ds``, ``y``, ``trend`` and [``yhat<i>``]

                Note
                ----
                where yhat<i> refers to the i-step-ahead prediction for this row's datetime.
                e.g. yhat3 is the prediction for this datetime, predicted 3 steps ago, "3 steps old".
        """
        if isinstance(df, dict):
            raise ValueError("Receiced more than one DataFrame. Use a for loop for many dataframes.")
        cols = ["ds", "y"]  # cols to keep from df
        df_forecast = pd.concat((df[cols],), axis=1)

        # create a line for each forecast_lag
        # 'yhat<i>' is the forecast for 'y' at 'ds' from i steps ago.
        for forecast_lag in range(1, self.n_forecasts + 1):
            forecast = predicted[:, forecast_lag - 1]
            pad_before = self.n_lags + forecast_lag - 1
            pad_after = self.n_forecasts - forecast_lag
            yhat = np.concatenate(([None] * pad_before, forecast, [None] * pad_after))
            df_forecast["yhat{}".format(forecast_lag)] = yhat
            df_forecast["residual{}".format(forecast_lag)] = yhat - df_forecast["y"]
        if components is None:
            return df_forecast

        # else add components
        lagged_components = [
            "ar",
        ]
        if self.config_covar is not None:
            for name in self.config_covar.keys():
                lagged_components.append("lagged_regressor_{}".format(name))
        for comp in lagged_components:
            if comp in components:
                for forecast_lag in range(1, self.n_forecasts + 1):
                    forecast = components[comp][:, forecast_lag - 1]
                    pad_before = self.n_lags + forecast_lag - 1
                    pad_after = self.n_forecasts - forecast_lag
                    yhat = np.concatenate(([None] * pad_before, forecast, [None] * pad_after))
                    df_forecast["{}{}".format(comp, forecast_lag)] = yhat

        # only for non-lagged components
        for comp in components:
            if comp not in lagged_components:
                forecast_0 = components[comp][0, :]
                forecast_rest = components[comp][1:, self.n_forecasts - 1]
                yhat = np.concatenate(([None] * self.n_lags, forecast_0, forecast_rest))
                df_forecast[comp] = yhat
        return df_forecast
