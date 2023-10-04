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

    const humidity_config = {
        type: 'gauge',
        data: {
          //labels: ['Success', 'Warning', 'Warning', 'Error'],
          datasets: [{
            label: "BME 280 Humidity",
            data: [10,20,30,50],
            value: [0],
            backgroundColor: ['red', 'orange', 'yellow', 'green'],
            borderWidth: 2
          }]
        },
        options: {
          responsive: true,
          needle: {
            // Needle circle radius as the percentage of the chart area width
            radiusPercentage: 2,
            // Needle width as the percentage of the chart area width
            widthPercentage: 3.2,
            // Needle length as the percentage of the interval between inner radius (0%) and outer radius (100%) of the arc
            lengthPercentage: 80,
            // The color of the needle
            color: 'rgba(0, 0, 0, 1)'
          },
        }
      }

    memory_config = {
        type: 'doughnut',
        data: {
            labels: ['Allocated Memory', 'Free Memory'],
            datasets: [{
                data: [50,100],
                backgroundColor: [
                    '#D3D3D3',
                    'green'
                ],
                borderColor: [
                    '#D3D3D3',
                    'green'
                ],
                borderWidth: 1
            }]
        },
        options: {
            legend: {
                display: false
            },
            tooltips: {
                enabled: true,
              },
        }
    }
    
    const context = document.getElementById('temperature_canvas').getContext('2d');
    const pressure_context = document.getElementById('pressure_canvas').getContext('2d');
    const humidity_context = document.getElementById('humidity_canvas').getContext('2d');
    const memory_context = document.getElementById('memory_canvas').getContext('2d');
    
    const lineChart = new Chart(context, temperature_config);
    const lineChart2 = new Chart(pressure_context, pressure_config);
    const gaugeChart = new Chart(humidity_context, humidity_config);
    const donutChart = new Chart(memory_context, memory_config);
    
    
      
        
    try {
        const source = new EventSource("/picow-stream-data");
        
        source.onmessage = function (event) {
            const data = JSON.parse(event.data);
            
            if(data == 'NoBrokersAvailable') {
                console.log(data)
                $('#kafka_offline').show()
                source.close()
                throw new Error('NoBrokersAvailable');
            }

            $('#live').show()
            
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

            humidity_config.data.datasets[0].value.fill(data.bme_280_humidity);
            gaugeChart.update();

            //string_array = [data.picow_mem_alloc_bytes, data.picow_mem_free_bytes]
            memory_config.data.datasets[0].data.fill(data.picow_mem_alloc_bytes,0)
            memory_config.data.datasets[0].data.fill(data.picow_mem_free_bytes,1)
            donutChart.update();

            $('#picow_local_ip').text(data.picow_local_ip)
            $('#picow_free_storage_kb').text(data.picow_free_storage_kb)
            $('#picow_free_cpu_freq_mhz').text(data.picow_free_cpu_freq_mhz)
        }
    } catch (e) {
        console.error(e);
        // Expected output: Error: Parameter is not a number!
      }
    
    
    });
