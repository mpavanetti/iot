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
            // Network Status
            $("#local_ip").append(`<span class='destroy'>${data.local_ip}</span>`);
            if(data.local_alive == true) {
                $("#local_alive").append(`<span class="badge bg-success destroy">ONLINE</span>`)
            } else {
                $("#local_alive").append(`<span class="badge bg-danger destroy">OFFLINE</span>`)
            }
            $("#picow_ip").append(`<span class='destroy'>${data.picow_ip}</span>`);
            if(data.picow_alive == true) {
                $("#picow_alive").append(`<span class="badge bg-success destroy">ONLINE</span>`)
            } else {
                $("#picow_alive").append(`<span class="badge bg-danger destroy">OFFLINE</span>`)
            }

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
            // pi4 hardware utilization
            $("#pi4_cpu").text(`${data.cpu_usage}%`);
            $("#pi4_mem").text(`${data.mem_usage}%`);
            $("#pi4_disk").text(`${data.disk_usage}%`);
    });

}

// Start document home.js
$( document ).ready(function() {
    console.log( "home.js loaded !" );

    load_host_hardware()
    check_host_status()

});