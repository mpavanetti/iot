$(document).ready(function () {
    const config = {
        type: 'line',
        data: {
            labels: Array(30).fill("0000-00-00 00:00:00"),
            datasets: [{
                label: "Temperature Celsius",
                backgroundColor: '#0d6efd',
                borderColor: '#0d6efd',
                data: Array(30).fill(null),
                fill: false,
            }],
        },
        options: {
            responsive: true,
            title: {
                display: true,
                text: 'BME 280 Sensor'
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
                        labelString: 'Values'
                    }
                }]
            }
        }
    };
    
    const context = document.getElementById('canvas').getContext('2d');
    
    const lineChart = new Chart(context, config);
    
    const source = new EventSource("/bme-280-temperature");
    
    source.onmessage = function (event) {
        const data = JSON.parse(event.data);
       //console.log(data)
        if (config.data.labels.length === 30) {
            config.data.labels.shift();
            config.data.datasets[0].data.shift();
        }
        config.data.labels.push(data.time);
        config.data.datasets[0].data.push(data.value);
        lineChart.update();
    }
    });