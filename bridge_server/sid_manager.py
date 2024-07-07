class SidManager:
    def __init__(self):
        self.sockets_res = None
        self.sockets_req = None
        self.linked_server = None
        self.wf_info = None
        self.ws_connection_status = None
        self.execution_info = None
        self.history_life = None

    async def release_sockets(self):
        if self.sockets_res is not None:
            await self.sockets_res.close()
        if self.sockets_req is not None:
            await self.sockets_req.close()
        self.sockets_res = None
        self.sockets_res = None
        self.ws_connection_status = None
        self.wf_info = None
    
    def release_info(self):
        if self.sockets_res is not None:
            self.sockets_res.close()
        if self.sockets_req is not None:
            self.sockets_req.close()
    
    def release_(self):
        if self.sockets_res is not None:
            self.sockets_res.close()
        if self.sockets_req is not None:
            self.sockets_req.close()