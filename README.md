# torr
### A Python package for download torrent files.

## How to use:

~~~python
from torr.bittorrent import TorrentClient

client = TorrentClient('~/Downloads/Big Buck Bunny (1920x1080 h.264).torrent')
client.start()
~~~

That's it.
The TorrentClient accepts the following parameters:

~~~python
TorrentClient(
    torrent: str,
    max_peers: int,
    output_dir: str,
)
~~~

* torrent: path to a .torent file
* max_peers: max peers should connect. after reaching this value, stop connecting to other peers. Big value will lead to
  more connected peers and therefore faster download speed, but this will make the handshake process slower. The
  default should be good for most uses (`12`)
* output_dir: path to the directory the output files should be saved. default is `None`

## Simple usage:

You can use cli interface

~~~bash
torr -h

usage: 
    Script for downloading torrent files
     [-h] --torrent TORRENT [--peers PEERS] [--output-directory OUTPUT_DIRECTORY] [--use-progress-bar] [--max-peers MAX_PEERS]

optional arguments:
  -h, --help            show this help message and exit
  --torrent TORRENT     Path of the Torrent file
  --output-directory OUTPUT_DIRECTORY
                        Path to the output directory
  --max-peers MAX_PEERS
                        Max connected peers
~~~

### Example of downloading torrent to "Downloads":

~~~bash
torr --torrent examples/alpine-minirootfs-3.23.3-aarch64.tar.gz.torrent --output-directory ~/Downloads
~~~

## Configuration

Non-existing options are ignored.

`_documentation`: just the link for the documentation, all keys starting with `_` are going to be ignored.  
`listening_port`: [port to announce to tracker](https://wiki.theory.org/BitTorrent_Tracker_Protocol#Basic_Tracker_Announce_Request).  
`max_peers`: maximun amount of peers to connect to.  
`interation_sleep_interval`: interval between each piece request.  
`logging_level`: [logging level for Logger](https://docs.python.org/3/library/logging.html#logging.Logger.setLevel).  
`timeout`: timeout for every request made.  
`max_handshake_threads`: Number of handshakes to make in each thread with fixed amount of peers. This value is inversely porportional to the number of threads created.  

### Experimental options
`udp_handshake_threads`: bytes to read from UDP tracker requests.  
`handshake_stripped_size`: value to receive from peer requests.  
`default_connection_id`: default connection ID for UDP tracker connections.  
`compact_value_num_bytes`: bytes to read each iteration of compacted peers response.  

## List of BitTorrent messages and their support

| Type           | supported | id  |
|----------------|-----------|-----|
| Handshake      | `yes`     | -   |
| Keep Alive     | `yes`     | 0   |
| Choke          | `yes`     | 1   |
| Unchoke        | `yes`     | 2   |
| Interested     | `no`      | 3   |
| Not interested | `no`      | 4   |
| BitField       | `yes`     | 5   |
| Request        | `yes`     | 6   |
| Piece          | `yes`     | 7   |
| Cancel         | `no`      | 8   |
| Port           | `no`      | 9   |

## The architecture of the program
* At first, we retrieve all available peers, using the trackers from the `torrent` file.
* Then, we try to connect each one of them, until the value of `max_peers` achieved. The handshakes happen in a *poll* of threads. each thread contain `MAX_HANDSHAKE_THREADS` of peers to handshake with. the `number_of_polls` calculated according to the length of the given peers divided by the `MAX_HANDSHAKE_THREADS`, so we can cover all the peers. note that this process happens in <mark>parallel to the other 2 threads</mark>. continue to read for more details.
* Right after launching the handshakes thread polls, we start listening for incomming messages using the `handle_messages` function, that calling the `receive_messages` in the `PeersManager` in his turn. This function will search for readable socket, and then parse the data to one of the `torr.message` classes. This contains one of the two main threads of the program, that continue until completion of the download. 
* Meanwhile we can start requesting for pieces. we do that by calling the function `piece_requester` in different threads. this function search after connected peer *(unchocked connected peer)* that have the piece index we currently in. **The current strategy for piece picking is what we can call *Asynchronous Chronological***. Means that we start requesting for piece index 0, 1, 2, until the end of the file, but don't stop the program if one of them is not full yet. **Better strategy can be implemented**, like *Rarest-Piece-First*, But i noticed that most of the torrent download process is in front of seeders, so i thought it will be useless right now. But of course in communicating with actual peers (like in a new torrent file that just explode over the internet), smarter strategies can help.
* After all pieces have been received, if the torrent file contain folders we create them and rewrite them in the correct order, until then, all files are written to temp file.
------
### Important notes:
* **We are a dirty Leecher:** The current implementation is a _leecher_. That's mean you can only *download* file, and not *upload* anything. You can conclude that other peers might see that in a bad eye and therefore give you a lower rate and bandwidth, and as a result you won't *unchoked* by them, resulting lower speed rate comparing to popular torrent clients. Keep in mind that the infrastructure for acting as peer/seeder has been laid, so implement it should be easy. - hope it will be changed soon ;)
* **statistic bug:** In the current architecture of the program, it is multi threaded. it seems that it cause some undefined bug in interacting with the `select` function in the `receive_messages` of the `PeersManager`. it means stuck messages, that get released after the stuck peer will send any message. You sometimes experience it when you see that all the peers are choked for few seconds/minutes, when in fact they are not.
* **over memory usage in big torrent:** if the given torrent file contain multiply folders, the actual content is written to a temp file using the tempfile library. afterwards, we read chunks in the size of each inner file, and write them to the correct path. this `read` calls can cause over memory use in big torrents. we can avoid that by not copy the bytes using this function, but using other only disk-operation function, like the `dd` command. I haven't found this one yet.
