.. _my-reference-label2:

Full Simple Model
==================

.. note::
  This page contains details of how you can build a simple model using NeuralProphet with minimal features.

Install
--------
After downloading the code repository (via :code:`git clone`), change to the repository directory (:code:`cd neural_prophet`) and install neuralprophet as python package with
:code:`pip install .`

.. note::
  If you plan to use the package in a Jupyter notebook, it is recommended to install the 'live' package version with :code:`pip install .[live]`.
  This will allow you to enable :code:`plot_live_loss` in the :code:`train` function to get a live plot of train (and validation) loss.

Import
-------

The input data format expected by the :code:`neural_prophet` package is the same as in original 
:code:`prophet`. It should have two columns, :code:`ds` which has the timestamps and :code:`y` column which
contains the observed values of the time series.

============  ====== 
ds             y    
============  ======  
2007-12-10     9.59 
2007-12-10     8.42 
2007-12-10     8.10   
2007-12-10     7.04  
2007-12-10     8.18   
============  ======  

Throughout this documentation, we will be using the time series data of the log daily page views for the `Peyton Manning <https://en.wikipedia.org/wiki/Peyton_Manning>`_ Wikipedia page. The data can be imported as follows.

.. code-block:: Python

  import pandas as pd

  data_location = "https://raw.githubusercontent.com/ourownstory/neuralprophet-data/main/datasets/"

  df = pd.read_csv(data_location + 'wp_log_peyton_manning.csv')

Simple Model 
-------------

A simple model with :code:`neural_prophet` for this dataset can be fitted by creating
an object of the :code:`NeuralProphet` class as follows and calling the fit function. This 
fits a model with the default settings in the model. Note that the frequency of data is set globally here. 
Valid timeseries frequency settings are `pandas timeseries offset aliases <https://pandas.pydata.org/pandas-docs/stable/user_guide/timeseries.html#timeseries-offset-aliases)>`_.

.. code-block:: Python

  m = NeuralProphet()
  metrics = m.fit(df, freq="D")

Once the model is fitted, we can make predictions using the fitted model. 
Here we are predicting in-sample over our data to evaluate the model fit.
We could do the same for a holdout set.

.. code-block:: Python 

  future = m.make_future_dataframe(df=df, periods=365)
  forecast = m.predict(df=future)

Plotting 
---------
Let's visualize the obtained forecast:

.. code-block:: Python 

  fig_forecast = m.plot(forecast)

.. image:: images/plot_forecast_simple_model_1.png 
  :align: center

This is a simple model with a trend, a weekly seasonality and a yearly seasonality estimated by default. 
You can also look at the individual components separately as below. 

.. code-block:: Python 

  fig_comp = m.plot_components(forecast)

.. image:: images/plot_comp_simple_1.png 
  :align: center

The individual coefficient values can also be plotted as below to gain further insights.

.. code-block:: Python 

  fig_param = m.plot_parameters()

.. image:: images/plot_param_simple_1.png 
  :align: center

Validation 
------------
There are two ways to perform model validation in NeuralProphet:


**1. Manual Split**

Users can split the dataset manually to validate after the model fitting like below by specifying the fraction of validation data. Thereby, the validation set is reserved from the end of the series.

.. code-block:: Python 

  m = NeuralProphet()
  df_train, df_test = m.split_df(df, valid_p=0.2)

You can now look at the training and validation metrics separately as below.

.. code-block:: Python 
  
  train_metrics = m.fit(df_train)
  test_metrics = m.test(df_test)

**2. Builtin Function**

Alternatively, you can perform validation per every epoch during model fitting as below. 

.. code-block:: Python 
  
  m = NeuralProphet()
  metrics = m.fit(df_train, validation_df=df_test)

Reproducibility
----------------
The variability of results comes from SGD finding different optima on different runs.
The majority of the randomness comes from the random initialization of weights, 
different learning rates and different shuffling of the dataloader.

Although, NeuralProphet allows you to control the random number generator by setting it's seed:

.. code-block:: Python 
  
  from neuralprophet import set_random_seed 
  set_random_seed(0)

This should lead to identical results every time you run the model. 
Note that you have to explicitly set the random seed to the same random number each time before fitting the model.

.. note::

  Congrats on completing the full simple model tutorial! 🥳 
  Let's solve some real world applications and head over to the advanced Tutorials to your left! 🏄🏼‍♂️



