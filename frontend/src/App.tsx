import { Link, Navigate, Route, Routes } from "react-router-dom";
import AdminPage from "./pages/admin/AdminPage";
import CandidateLogin from "./pages/candidate/CandidateLogin";
import Done from "./pages/candidate/Done";
import FaceRegCallback from "./pages/candidate/FaceRegCallback";
import Precheck from "./pages/candidate/Precheck";
import TakeExam from "./pages/candidate/TakeExam";
import AttemptDetail from "./pages/proctor/AttemptDetail";
import Collusion from "./pages/proctor/Collusion";
import Dashboard from "./pages/proctor/Dashboard";
import LeakTrace from "./pages/proctor/LeakTrace";
import Metrics from "./pages/proctor/Metrics";
import ReviewQueue from "./pages/proctor/ReviewQueue";
import StaffLogin from "./pages/StaffLogin";

function Home() {
  return (
    <main>
      <div className="card">
        <h2>ExamGuard · Hệ thống thi trực tuyến</h2>
        <p>Hệ thống chỉ <b>hỗ trợ</b> giám thị phát hiện bất thường; giám thị luôn là người kết luận cuối cùng.</p>
        <div className="row">
          <Link to="/exam/login" className="shrink"><button>Thí sinh vào thi</button></Link>
          <Link to="/staff/login" className="shrink"><button className="secondary">Giám thị / Quản trị</button></Link>
        </div>
      </div>
    </main>
  );
}

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<Home />} />
      <Route path="/exam/login" element={<CandidateLogin />} />
      <Route path="/exam/check" element={<Precheck />} />
      <Route path="/exam/facereg-callback" element={<FaceRegCallback />} />
      <Route path="/exam/take" element={<TakeExam />} />
      <Route path="/exam/done" element={<Done />} />
      <Route path="/staff/login" element={<StaffLogin />} />
      <Route path="/admin" element={<AdminPage />} />
      <Route path="/proctor" element={<Dashboard />} />
      <Route path="/proctor/attempt/:id" element={<AttemptDetail />} />
      <Route path="/proctor/review" element={<ReviewQueue />} />
      <Route path="/proctor/metrics" element={<Metrics />} />
      <Route path="/proctor/collusion" element={<Collusion />} />
      <Route path="/proctor/leak" element={<LeakTrace />} />
      <Route path="*" element={<Navigate to="/" />} />
    </Routes>
  );
}
