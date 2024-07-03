from ai_api import (queue_prompt,
                    get_history,
                    delete_history,
                    get_queue_state,
                    get_parsed_input_nodes,
                    parse_workflow_prompt,
                    parse_outputs,
                    save_binary_file,
                    AsyncJsonWrapper)
import asyncio
import aiohttp
import logging
from aiohttp import web
import json, os

class BridgeServer():
    
    def __init__(self, loop, config_fn,server_address:list) -> None:
        self.loop = loop
        self.sockets_res = {}
        self.sockets_req = {}
        self.sid_server_map = {}
        self.wf_info = {}
        self.ws_connection_status = {}
        self.server_address = server_address

        self.config_obj = AsyncJsonWrapper(config_fn)

    async def send_socket_catch_exception(self, sid, message):
        try:
            await self.sockets_res[sid].send_json(message)
            self.ws_connection_status[sid] = message.get("status", None)
            logging.debug(f"[WS] SEND OK / {message} / {sid}")
        except Exception as err:
            self.ws_connection_status[sid] = "error"
            logging.debug(f"[WS]] SEND FAILED / {err} / {message} / {sid}")

    async def init_app(self):
        app = web.Application(client_max_size=1024**2*100)  # 업로드 용량 확인 middlek
        app.add_routes([
            web.post('/generate-based-workflow', self.generate_based_workflow),
            web.get('/ws', self.websocket_connection),
            web.get("/", self.main_page),
            web.get("/history", self.get_history),
            web.get("/workflow-list", self.get_workflow_list),
            web.get("/generation-count", self.get_generation_count),
            web.post("/upload/image", self.upload_image),
            web.post("/workflow-info", self.workflow_info),
        ])

        await self.config_obj.load()
        return app
    
    async def track_progress(self, sid):
        total_progress = 0
        cur_progress = 0

        logging.info(f"[WS] TRACING START / {sid}")
        while True:
            out = await self.sockets_req[sid].receive()
            out = out.data

            if self.ws_connection_status[sid] == "closed" or self.ws_connection_status[sid] == "error":
                break

            if isinstance(out, str):
                message = json.loads(out)
                
                if message['type'] == 'execution_start':
                    logging.info(f"[WS] EXECUTION START / {sid}")

                    wf_info = self.wf_info.get(sid, None)
                    inputs_infos = [cur.get("inputs", None) for cur in wf_info.values()]
                    steps_list = [value for inputs_info in inputs_infos
                        for key, value in inputs_info.items()
                        if "steps" in key]
                    total_progress += (len(wf_info) + sum(steps_list))
                    progress_message = {
                        'status': 'progress',
                        'details': f'{cur_progress/total_progress*100:.2f}%'
                    }
                    await self.send_socket_catch_exception(sid, progress_message)
                
                if message['type'] in ('progress', 'executing'):
                    data = message['data']

                    if data['node'] is None and data['prompt_id'] == sid:
                        progress_message = {
                            'status': 'closed',
                            'details': 'Execution is done'
                        }
                    else:
                        cur_progress += 1
                        progress_message = {
                            'status': 'progress',
                            'details': f'{cur_progress/total_progress*100:.2f}%'
                        }
                    await self.send_socket_catch_exception(sid, progress_message)

                if message['type'] == 'execution_cached':
                    logging.debug(f"[WS] EXECUTION DONE / {sid}")
                    
                    cached_nodes = message['data']['nodes']
                    cur_progress += len(cached_nodes)
                    progress_message = {
                        'status': 'progress',
                        'details': f'{cur_progress/total_progress*100:.2f}%'
                    }
                    await self.send_socket_catch_exception(sid, progress_message)
            else:
                continue
        logging.info(f"[WS] TRACING DONE / {sid}")

        return


    async def websocket_connection(self, request):
        sid = request.rel_url.query.get('clientId', '')

        logging.info(f"[WS] RECEIVED / {sid}")
        
        async with aiohttp.ClientSession() as session:
            server_address = await self.get_not_busy_server_address()
            self.sid_server_map[sid] = server_address
            logging.debug(f"[WS] server allocated to {server_address} / {sid}")
            
            ws_res = web.WebSocketResponse()
            ws_req = await session.ws_connect(f"ws://{server_address}/ws?clientId={sid}")
            logging.info(f"[WS] HANDSHAKE / {sid}")

            self.sockets_res[sid] = ws_res
            self.sockets_req[sid] = ws_req

            try:
                await self.sockets_res[sid].prepare(request)
                logging.info(f"[WS] BRIDGE CONNECTED / {sid}")

                await self.send_socket_catch_exception(sid, {"status":"connected", "details":"web socket connected"})
                self.ws_connection_status[sid] = "connected"
                
                task = asyncio.create_task(self.track_progress(sid))

                timeout_count = 0
                limit_timeout_count = 100
                time_out_interval = 5
                while True:
                    if self.ws_connection_status[sid] == "closed" or self.ws_connection_status[sid] == "error":
                        break
                    else:
                        await self.send_socket_catch_exception(sid, {"status":"listening", "details":"server is listening"})
                        if timeout_count >= limit_timeout_count:
                            raise TimeoutError(f"timeout count: {timeout_count}")
                        timeout_count += 1

                    await asyncio.sleep(time_out_interval)
                await task

            except aiohttp.ServerDisconnectedError as e:
                logging.warning(f"[WS] SERVER DISCONNECTED ERROR / {sid}")
                await self.send_socket_catch_exception(sid, {"status":"error", "details":"server disconnected"})
            except TimeoutError as e:
                logging.warning(f"[WS] TIMEOUT ERROR / {sid}")
                await self.send_socket_catch_exception(sid, {"status":"error", "details":f"time out error: exceed {limit_timeout_count * time_out_interval}s"})
            except Exception as e:
                logging.error(f"[WS] UNKNOWN ERROR / {sid}")
                await self.send_socket_catch_exception(sid, {"status":"error", "details":str(e)})

            finally:
                logging.info(f"[WS] CLOSED / {sid}")
                await self.send_socket_catch_exception(sid, {"status":"closed", "details":"connection will be closed"})
                await self.sockets_req[sid].close()
                await self.sockets_res[sid].close()
                self.sockets_req.pop(sid, None)
                self.sockets_res.pop(sid, None)
                self.ws_connection_status.pop(sid, None)

    async def get_not_busy_server_address(self):
        queue_lenghs = []
        for server_address in self.server_address:
            try:
                queue_state = get_queue_state(server_address)
            except Exception as e:
                logging.debug(f"[NO SIGNAL] {server_address} / {e}")

            queue_length = sum([len(cur) for cur in queue_state.values()])
            queue_lenghs.append(queue_length)
        
        target_server_address = self.server_address[queue_lenghs.index(min(queue_lenghs))]

        return target_server_address
    
    async def generate_based_workflow(self, request):
        data = await request.json()
        sid = request.rel_url.query.get('clientId', '')

        workflow = data.pop("workflow", None)
        kwargs = data
    
        prompt = parse_workflow_prompt(workflow, **kwargs)
        self.wf_info[sid] = prompt
        prompt = queue_prompt(prompt, sid, self.sid_server_map[sid])

        self.config_obj.generation_count += 1
        await self.config_obj.update()

        return web.Response(status=200)
    
    async def upload_image(self, request):
        reader = await request.multipart()

        logging.info(f"[POST] '{request.path}'")

        fns={}
        async for part in reader:

            try:
                file_name = part.headers.get('Content-Disposition', '').split('filename=')[1].strip('"')
                file_data = await part.read()
            
                fn = save_binary_file(file_data, file_name)
                fns[part.headers.get("ori_file_id", None)] = fn
                logging.debug(f"[POST] '{request.path}' / {fn} saved")
        
            except Exception as e:
                logging.error(f"[POST] '{request.path}' / {file_name} can't save / {str(e)}")

                return web.Response(
                    status=400,
                    body=json.dumps({"detail":f"{file_name} can't save"}),
                    headers={"Content-Type": "application/json"}
                )

        return web.Response(
            status=200,
            body=json.dumps(fns),
            headers={"Content-Type": "application/json"}
        )
        
    async def get_history(self, request):
        sid = request.rel_url.query.get('clientId', '')

        history = get_history(sid, self.sid_server_map[sid])
        logging.debug(f"[GET] '{request.path}' / GET HISTORY / {sid}")
        delete_history(sid, self.sid_server_map[sid])
        logging.debug(f"[GET] '{request.path}' / DELETE HISTORY / {sid}")

        history = history.get(sid, None)
        self.sid_server_map.pop(sid, None)

        if history is not None:
            output = history["outputs"],
            if isinstance(output, tuple):
                output = (output)
            
            writer = aiohttp.MultipartWriter("form-data")
            
            file_paths, mime_types, file_contents = parse_outputs(output[0])
            for file_path, mime_type, file_content in zip(file_paths, mime_types, file_contents):

                headers = {'Content-Type': mime_type, 'Content-Disposition': f'attachment; filename="{file_path.split("/")[-1]}"'}
                writer.append(file_content, headers)

            headers = {
                'Content-Type': writer.content_type,
            }
            
            return web.Response(
                status=200,
                body=writer,
                headers=headers
            )
        else:
            return web.Response(
                status=204,
                body=json.dumps({"detail":f"No contents with that client id / {sid}"}),
                headers={"Content-Type": "application/json"}
            )
    
    async def workflow_info(self, request):
        data = await request.json()

        workflow = data.pop("workflow", None)
        node_info = get_parsed_input_nodes(workflow)

        logging.debug(f"[GET] '{request.path}' / GET PARSED WORKFLOW INFO")

        return web.Response(status=200, body=json.dumps(node_info), content_type="application/json")
    
    async def get_workflow_list(self, request):
        wf_list = os.listdir("./workflows")
        return web.Response(status=200, body=json.dumps(wf_list), content_type="application/json")

    async def get_generation_count(self, request):
        generation_count = self.config_obj.generation_count
        return web.Response(status=200, body=json.dumps(generation_count), content_type="application/json")

    async def main_page(self, request):
        return web.Response(text="Hello, this is Favorfit Bridge Server!")

def main():
    from dotenv import load_dotenv
    load_dotenv()
    
    servers_str = os.getenv('COMFYUI_SERVERS')
    host = os.getenv("HOST")
    port = os.getenv("PORT")
    config_fn = os.getenv("CONFIG")
    logging_level = os.getenv("LOGGING_LEVEL", "INFO").upper()

    logging.basicConfig(level=getattr(logging, logging_level, logging.INFO),
                        format='%(asctime)s - %(levelname)s - %(message)s',
                        handlers=[logging.StreamHandler()])
    
    server_list = servers_str.split(',') if servers_str else []

    loop = asyncio.get_event_loop()
    server = BridgeServer(loop=loop, config_fn=config_fn, server_address=server_list)
    app = loop.run_until_complete(server.init_app())
    web.run_app(app, host=host, port=int(port))
    

if __name__ == '__main__':
    main()