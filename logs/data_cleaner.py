def data_cleaner(data_raw):
    import pandas as pd
    import numpy as np
    from sklearn.impute import SimpleImputer

    # Step 1: Remove columns with more than 40% missing values
    threshold = 0.4 * len(data_raw)
    data_cleaned = data_raw.dropna(thresh=threshold, axis=1)

    # Step 2: Impute missing values
    for column in data_cleaned.columns:
        if data_cleaned[column].isnull().sum() > 0:
            if data_cleaned[column].dtype == 'object':
                # Impute categorical columns with mode
                imputer = SimpleImputer(strategy='most_frequent')
                data_cleaned[column] = imputer.fit_transform(data_cleaned[[column]]).ravel()
            else:
                # Impute numeric columns with mean
                imputer = SimpleImputer(strategy='mean')
                data_cleaned[column] = imputer.fit_transform(data_cleaned[[column]]).ravel()

    # Step 3: Convert columns to the correct data type
    data_cleaned['TotalCharges'] = pd.to_numeric(data_cleaned['TotalCharges'], errors='coerce')

    # Step 4: Remove duplicate rows
    data_cleaned = data_cleaned.drop_duplicates()

    # Step 5: Remove rows with any missing values
    data_cleaned = data_cleaned.dropna()

    # Step 6: Remove rows with extreme outliers
    for column in data_cleaned.select_dtypes(include=[np.number]).columns:
        Q1 = data_cleaned[column].quantile(0.25)
        Q3 = data_cleaned[column].quantile(0.75)
        IQR = Q3 - Q1
        lower_bound = Q1 - 3 * IQR
        upper_bound = Q3 + 3 * IQR
        data_cleaned = data_cleaned[(data_cleaned[column] >= lower_bound) & (data_cleaned[column] <= upper_bound)]

    return data_cleaned