$(document).ready(function () {
    const temperature_config = {
        type: 'line',
        data: {
            labels: Array(30).fill("0000-00-00 00:00:00"),
            datasets: [{
                label: "BME 280 Temperature",
                backgroundColor: '#0d6efd',
                borderColor: '#0d6efd',
                data: Array(30).fill(null),
                fill: false,
            },
            {
                label: "Pico W Temperature",
                backgroundColor: '#712cf9',
                borderColor: '#712cf9',
                data: Array(30).fill(null),
                fill: false,
            }],
        },
        options: {
            responsive: true,
            title: {
                display: true,
                text: 'Overall Sensors Temperature Graph'
            },
            tooltips: {
                mode: 'index',
                intersect: false,
            },
            hover: {
                mode: 'nearest',
                intersect: true
            },
            scales: {
                xAxes: [{
                    display: true,
                    scaleLabel: {
                        display: true,
                        labelString: 'Time (UTC)'
                    }
                }],
                yAxes: [{
                    display: true,
                    scaleLabel: {
                        display: true,
                        labelString: 'Temperature (Celcius)'
                    }
                }]
            }
        }
    };

    const pressure_config = {
        type: 'line',
        data: {
            labels: Array(30).fill("0000-00-00 00:00:00"),
            datasets: [{
                label: "BME 280 Pressure",
                backgroundColor: '#119157',
                borderColor: '#119157',
                data: Array(30).fill(null),
                fill: false,
            }],
        },
        options: {
            responsive: true,
            title: {
                display: true,
                text: 'BME 280 Atmospheric Pressure Graph'
            },
            tooltips: {
                mode: 'index',
                intersect: false,
            },
            hover: {
                mode: 'nearest',
                intersect: true
            },
            scales: {
                xAxes: [{
                    display: true,
                    scaleLabel: {
                        display: true,
                        labelString: 'Time (UTC)'
                    }
                }],
                yAxes: [{
                    display: true,
                    scaleLabel: {
                        display: true,
                        labelString: 'Pressure (hPa)'
                    }
                }]
            }
        }
    };
    
    const context = document.getElementById('temperature_canvas').getContext('2d');
    const pressure_context = document.getElementById('pressure_canvas').getContext('2d');
    
    const lineChart = new Chart(context, temperature_config);
    const lineChart2 = new Chart(pressure_context, pressure_config);
    
    const source = new EventSource("/picow-stream-data");
    
    source.onmessage = function (event) {
        const data = JSON.parse(event.data);
       //console.log(data)
        if (temperature_config.data.labels.length === 30) {
                temperature_config.data.labels.shift();
                temperature_config.data.datasets[0].data.shift();
                temperature_config.data.datasets[1].data.shift();
            }

        temperature_config.data.labels.push(data.time);
        temperature_config.data.datasets[0].data.push(data.bme_280_temperature);
        temperature_config.data.datasets[1].data.push(data.picow_temperature);
        lineChart.update();

        if (pressure_config.data.labels.length === 30) {
            pressure_config.data.labels.shift();
            pressure_config.data.datasets[0].data.shift();
        }

        pressure_config.data.labels.push(data.time);
        pressure_config.data.datasets[0].data.push(data.bme_280_pressure);
        lineChart2.update();
    }
    
    });