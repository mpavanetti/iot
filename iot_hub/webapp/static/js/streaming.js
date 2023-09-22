$(document).ready(function () {
    const config = {
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
    
    const context = document.getElementById('canvas').getContext('2d');
    
    const lineChart = new Chart(context, config);
    
    const source = new EventSource("/picow-stream-data");
    
    source.onmessage = function (event) {
        const data = JSON.parse(event.data);
       //console.log(data)
        if (config.data.labels.length === 30) {
            config.data.labels.shift();
            config.data.datasets[0].data.shift();
            config.data.datasets[1].data.shift();
        }
        config.data.labels.push(data.time);
        config.data.datasets[0].data.push(data.bme_280_temperature);
        config.data.datasets[1].data.push(data.picow_temperature);
        lineChart.update();
    }
    });