// Function for loading infrastructure services status.
function check_kafka() {
    
    // api call 
    $.ajax({
        url: "/api/kafka_info",
        context: document.body,
    }).done(function(data) {
        $.each(data.describe_topics, function(index, value) {
            $(`#kafka_cluster`).append(`
                <tr>
                    <td>${value.topic}</td>
                    <td>${((value.error_code == 0) ? 'Good' : 'Bad') }</td>
                  </tr>
            `)

        });
    });
}

// Function for loading infrastructure services status.
function check_infrastructure() {

    // loop 1 minute
    setTimeout(check_infrastructure,60000);

    // api call 
    $.ajax({
        url: "/api/check_ports",
        context: document.body,
        beforeSend : function(){
                $('.destroy2').remove()
            },
    }).done(function(data) {
        $('#refresh').show()

        $.each(data, function(index, value) {
            if(value == true) {
                $(`#${index}`).append(`<span class="badge bg-success destroy2">ONLINE</span>`)
            } else {
                $(`#${index}`).append(`<span class="badge bg-danger destroy2">OFFLINE</span>`)
            }
        }); 
    });
}

// Function for loading host status.
function check_host_status() {

    // loop 5 minutes
    setTimeout(check_host_status,300000);

    // api call 
    $.ajax({
        url: "/api/check_host_status",
        context: document.body,
        beforeSend : function(){
                // Show image container
                $('.spinner-border').show()
                $('.destroy').remove()
            },
    }).done(function(data) {
            $.each(data, function(index, value) {
                $(`#${index}_ip`).text(value.ip)
                if(value.alive == true) {
                    $(`#${index}`).append(`<span class="badge bg-success destroy">ONLINE</span>`)
                } else {
                    $(`#${index}`).append(`<span class="badge bg-danger destroy">OFFLINE</span>`)
                }
            });
            $('.spinner-border').hide()    
    });
}

function load_host_hardware() {
    // loop 5 seconds
    setTimeout(load_host_hardware,5000);

    // api call 
    $.ajax({
        url: "/api/load_host_hardware",
        context: document.body,
    }).done(function(data) {
            $("#live").show()
            // pi4 hardware utilization
            $("#pi4_cpu").text(`${data.cpu_usage}%`);
            $("#pi4_mem").text(`${data.mem_usage}%`);
            $("#pi4_disk").text(`${data.disk_usage}%`);
    });

}

// Start document home.js
$( document ).ready(function() {
    console.log( "home.js loaded !" );

    check_infrastructure()
    check_kafka()
    load_host_hardware()
    check_host_status()
    

});