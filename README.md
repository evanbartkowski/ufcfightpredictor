# UFC Fight Predictor Overview
This project leverages historical UFC fight data and fighter statistics to predict the outcomes of future matches. It has applications in data analysis such as identifying key stats and fight styles, sports betting, and satisfying general curiosity. By utilizing AI models such as Deep Neural Networks and Gradient Boosting, predictions can be made faster and more intelligently.

# Datasets
fighter_stats.csv – Contains individual statistics for each UFC fighter with 16 columns of data.

large_dataset.csv – The largest dataset, featuring over 7,000 rows and 94 columns. Includes detailed fight outcomes and fighter metrics.

ufc-master.csv – Contains around 1,600 rows and 118 columns, including additional details like betting odds.

# Model Insights
After experimenting with both Gradient Boosting and Neural Networks for binary classification (predicting fight winners), Gradient Boosting consistently outperformed Neural Networks in terms of accuracy. While neural nets came close, they never fully matched the performance of Gradient Boost.

In terms of dataset performance:

The large_dataset.csv yielded the highest accuracy, likely due to its size and specificity.

The ufc-master.csv shows potential, and efforts are ongoing to improve model performance with it.

Combining datasets so far has reduced accuracy, possibly due to overlapping or conflicting features.

# Running your own tests on the model
To test or generate new predictions:

Navigate to the bottom of the UFC_GRADIENT_BOOST_PREDICTOR notebook.

Use the second-to-last cell to manually input fighter stats.

Use the last cell to pass an entire feature array for prediction.
