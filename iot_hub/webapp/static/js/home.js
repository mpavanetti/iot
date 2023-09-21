// Start document home.js
$( document ).ready(function() {
    console.log( "home.js loaded !" );

    $.ajax({
        url: "/api/load_home_data",
        context: document.body
      }).done(function(data) {
            $( this ).addClass( "done" );
            console.log(data)

            // Host Status

            // Network Status
            $("#local_ip").append(`<span>${data.local_ip}</span>`);
            if(data.local_alive == true) {
                $("#local_alive").append(`<span class="badge bg-success">ONLINE</span>`)
            } else {
                $("#local_alive").append(`<span class="badge bg-danger">OFFLINE</span>`)
            }
            $("#picow_ip").append(`<span>${data.picow_ip}</span>`);
            if(data.picow_alive == true) {
                $("#picow_alive").append(`<span class="badge bg-success">ONLINE</span>`)
            } else {
                $("#picow_alive").append(`<span class="badge bg-danger">OFFLINE</span>`)
            }

            // pi4 hardware utilization
            $("#pi4_cpu").append(`<span>${data.cpu_usage}%</span>`);
            $("#pi4_mem").append(`<span>${data.mem_usage}%</span>`);
            $("#pi4_disk").append(`<span>${data.disk_usage}%</span>`);
            
      });

});