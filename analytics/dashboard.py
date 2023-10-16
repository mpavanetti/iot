import streamlit as st
import pandas as pd
import plost
from functions import *

# General configs
st.set_page_config(layout='wide', initial_sidebar_state='expanded', page_title='IoT Analytics', page_icon = "img/analytics.png")
with open('style.css') as f:
    st.markdown(f'<style>{f.read()}</style>', unsafe_allow_html=True)

# SideBar
st.sidebar.header('Dashboard `Overview`')

st.sidebar.subheader('Heat map parameter')
time_hist_color = st.sidebar.selectbox('Temperature sensor by:', ('avg_bme_temp', 'avg_picow_temp')) 

st.sidebar.subheader('Line chart parameters')
plot_data = st.sidebar.multiselect('Select data', ['min_bme_temp', 'avg_bme_temp' ,'max_bme_temp','min_picow_temp','max_picow_temp','avg_picow_temp'], ['min_bme_temp', 'avg_bme_temp' , 'max_bme_temp'])

st.sidebar.markdown('''
---
Created by [Matheus Pavanetti](https://github.com/mpavanetti/).
''')

# Main Content
st.write("# IoT Analytics :bar_chart:")

dataset = transform_agg_by_hour(load_agg_by_hour())
col1, col2, col3 = st.columns(3)
col1.metric("Temperature", f"{dataset['avg_bme_temp']} °C", f"{dataset['diff_avg_bme_temp']} °C")
col2.metric("Atmospheric Pressure", f"{dataset['avg_bme_press']} hPa", f"{dataset['diff_avg_bme_press']}%")
col3.metric("Humidity", f"{dataset['avg_bme_hum']}%", f"{dataset['diff_avg_bme_hum']}%")

st.write("Last hour temperature, pressure and humidity average compared to last but one hour.")

c1, c2 = st.columns((7,3))
with c1:
    st.markdown('### Temperature Heatmap')
    plost.time_hist(
    data=load_agg_by_hour(),
    date='datetime',
    x_unit='hours',
    y_unit='day',
    color=time_hist_color,
    aggregate=None,
    legend=None,
    height=345,
    use_container_width=True)
with c2:
    st.markdown('### Data Origin')
    plost.donut_chart(
        data=load_agg_by_ip(),
        theta="messages",
        color='source_ip',
        legend='bottom', 
        use_container_width=True)

# Row C
st.markdown('### Temperature Line chart')
filter_plotly_line_chart(dataset=load_agg_by_hour(), metrics=plot_data)

# Dataset Section.
st.markdown("""---""")
st.subheader("Aggregated Dataset")
st.dataframe(filter_dataframe(load_agg_by_hour()))
