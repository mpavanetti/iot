import streamlit as st
import pandas as pd
from pandas.api.types import (
    is_categorical_dtype,
    is_datetime64_any_dtype,
    is_numeric_dtype,
    is_object_dtype,
)
import plotly.express as px
from sqlalchemy import create_engine

# Mariadb SQL Engine
engine = create_engine("mariadb+pymysql://mysql:mysql@192.168.1.80/data?charset=utf8mb4")

# Read Dataframe with pandas load_agg_by_hour
#@st.cache_data
def load_agg_by_hour():
    return pd.read_sql("SELECT * FROM agg_by_hour_df", engine)

# Read Dataframe with pandas load_agg_by_ip
#@st.cache_data
def load_agg_by_ip():
    return pd.read_sql("SELECT * FROM agg_by_ip_df", engine)

# Row A - BME Sensor Statistics
def transform_agg_by_hour(df: pd.Series):
    last_hour = df.iloc[0]
    last_second_hour = df.iloc[1]
    
    dataset = {
        "avg_bme_temp": last_hour["avg_bme_temp"],
        "avg_bme_hum": last_hour["avg_bme_hum"],
        "avg_bme_press": round(last_hour["avg_bme_press"],1),
        "diff_avg_bme_temp": round(last_second_hour["avg_bme_temp"] - last_hour["avg_bme_temp"],3),
        "diff_avg_bme_hum": round(last_second_hour["avg_bme_hum"] - last_hour["avg_bme_hum"],3),
        "diff_avg_bme_press": round(last_second_hour["avg_bme_press"] - last_hour["avg_bme_press"],3)
        
    }
    return dataset

def filter_plotly_line_chart(dataset, metrics):    
    # Filter
    filter_columns = st.selectbox("Filter by:", ("datetime","hour","date","day","month","year"))
    
    fig = px.line(dataset, x=filter_columns, y=metrics,
                title="Temperature Metrics", 
                markers=True,
                color_discrete_sequence=["skyblue", "teal", "tomato", "yellowgreen", "violet","turquoise"])
    
    fig.update_layout(
        showlegend=False,
    )

    st.plotly_chart(fig, use_container_width=True, theme=None)

def filter_line_chart(dataset, metrics):
    
    # Filter
    to_filter_columns = st.selectbox("Filter by:", ("datetime","hour","date","day","month","year"))
    
    # Plot Chart.
    st.line_chart(dataset, x = to_filter_columns, y = metrics)


def filter_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """
    Adds a UI on top of a dataframe to let viewers filter columns

    Args:
        df (pd.DataFrame): Original dataframe

    Returns:
        pd.DataFrame: Filtered dataframe
    """
    modify = st.checkbox("Add filters")

    if not modify:
        return df

    df = df.copy()

    # Try to convert datetimes into a standard format (datetime, no timezone)
    for col in df.columns:
        if is_object_dtype(df[col]):
            try:
                df[col] = pd.to_datetime(df[col])
            except Exception:
                pass

        if is_datetime64_any_dtype(df[col]):
            df[col] = df[col].dt.tz_localize(None)

    modification_container = st.container()

    with modification_container:
        to_filter_columns = st.multiselect("Filter dataframe on", df.columns)
        for column in to_filter_columns:
            left, right = st.columns((1, 20))
            # Treat columns with < 10 unique values as categorical
            if is_categorical_dtype(df[column]) or df[column].nunique() < 10:
                user_cat_input = right.multiselect(
                    f"Values for {column}",
                    df[column].unique(),
                    default=list(df[column].unique()),
                )
                df = df[df[column].isin(user_cat_input)]
            elif is_numeric_dtype(df[column]):
                _min = float(df[column].min())
                _max = float(df[column].max())
                step = (_max - _min) / 100
                user_num_input = right.slider(
                    f"Values for {column}",
                    min_value=_min,
                    max_value=_max,
                    value=(_min, _max),
                    step=step,
                )
                df = df[df[column].between(*user_num_input)]
            elif is_datetime64_any_dtype(df[column]):
                user_date_input = right.date_input(
                    f"Values for {column}",
                    value=(
                        df[column].min(),
                        df[column].max(),
                    ),
                )
                if len(user_date_input) == 2:
                    user_date_input = tuple(map(pd.to_datetime, user_date_input))
                    start_date, end_date = user_date_input
                    df = df.loc[df[column].between(start_date, end_date)]
            else:
                user_text_input = right.text_input(
                    f"Substring or regex in {column}",
                )
                if user_text_input:
                    df = df[df[column].astype(str).str.contains(user_text_input)]

    return df

